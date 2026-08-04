#nullable enable

using System;
using System.Net.Http;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Emby.Server.MediaEncoding.Api;
using MediaBrowser.Controller;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Controller.Net;
using MediaBrowser.Controller.Session;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.MediaInfo;
using MediaBrowser.Model.Serialization;
using MediaBrowser.Model.Services;

namespace Emby.Plugin.BlackBarrHelper.Playback
{
    [Route("/Items/{Id}/PlaybackInfo", "POST")]
    [Route("/PlaybackInfo", "POST")]
    public class BlackBarrPlaybackInfoService : IService
    {
        private static readonly HttpClient _httpClient = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };

        public BlackBarrPlaybackInfoService()
        {
        }

        public async Task<object> Post(GetPostedPlaybackInfo request)
        {
            var appHost = PlaybackEntryPoint.Instance?.AppHost;
            var logManager = PlaybackEntryPoint.Instance?.LogManager;
            var logger = PlaybackEntryPoint.Instance?.Logger;

            logger?.Info("[BlackBarr Helper] Intercepted PlaybackInfo request for Id={0}", request?.Id);

            if (appHost == null)
            {
                logger?.Warn("[BlackBarr Helper] AppHost is null in BlackBarrPlaybackInfoService");
                return new PlaybackInfoResponse();
            }

            var mediaSourceManager = GetProp<IMediaSourceManager>(appHost, "MediaSourceManager");
            var json = GetProp<IJsonSerializer>(appHost, "JsonSerializer");
            var ffmpegManager = GetProp<IFfmpegManager>(appHost, "FfmpegManager");
            var sessionContext = GetProp<ISessionContext>(appHost, "SessionContext");
            var sessionManager = GetProp<ISessionManager>(appHost, "SessionManager");
            var subtitleFontsManager = GetProp<ISubtitleFontsManager>(appHost, "SubtitleFontsManager");

            var innerService = new MediaInfoService(mediaSourceManager, json, logManager, appHost, ffmpegManager, sessionContext, sessionManager, subtitleFontsManager);
            var result = await innerService.Post(request).ConfigureAwait(false);

            if (result is PlaybackInfoResponse response && response.MediaSources != null)
            {
                logger?.Info("[BlackBarr Helper] Evaluating PlaybackInfoResponse with {0} media sources", response.MediaSources.Length);
                ApplyTranscodeOverride(response, logger);
            }

            return result;
        }

        private T? GetProp<T>(object target, string name) where T : class
        {
            try
            {
                var p = target.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                return p?.GetValue(target) as T;
            }
            catch
            {
                return null;
            }
        }

        private void ApplyTranscodeOverride(PlaybackInfoResponse response, ILogger? logger)
        {
            var config = Plugin.Instance?.Configuration;
            if (config == null) return;

            bool forceAll = config.ForceTranscodeAll;
            bool forceCropped = config.ForceTranscodeCropped;

            if (!forceAll && !forceCropped) return;

            if (response.MediaSources == null) return;

            foreach (var ms in response.MediaSources)
            {
                if (ms == null) continue;

                string? filePath = ms.Path;
                bool shouldForce = forceAll;

                if (!shouldForce && forceCropped && !string.IsNullOrEmpty(filePath))
                {
                    shouldForce = CheckItemCropped(config.BlackBarrUrl, filePath!);
                }

                if (shouldForce)
                {
                    logger?.Info("[BlackBarr Helper] ENFORCING transcode on PlaybackInfoResponse for Path={0}", filePath);
                    ms.SupportsDirectPlay = false;
                    ms.SupportsDirectStream = false;
                    ms.SupportsTranscoding = true;
                    ms.DirectStreamUrl = null;

                    if (!string.IsNullOrEmpty(ms.TranscodingUrl))
                    {
                        ms.TranscodingUrl = Regex.Replace(
                            ms.TranscodingUrl,
                            @"((?:MaxStreamingBitrate|VideoBitrate|MaxBitrate)=)\d+",
                            "${1}140000000",
                            RegexOptions.IgnoreCase);
                    }
                }
            }
        }

        private bool CheckItemCropped(string blackbarrUrl, string filePath)
        {
            try
            {
                var requestUrl = $"{blackbarrUrl.TrimEnd('/')}/api/crop_val?path={Uri.EscapeDataString(filePath)}";
                var task = _httpClient.GetAsync(requestUrl);
                var resp = task.GetAwaiter().GetResult();
                if (resp.IsSuccessStatusCode)
                {
                    var content = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
                    return content.Contains("crop=");
                }
            }
            catch
            {
                // Ignore API connection failures
            }
            return false;
        }
    }
}
