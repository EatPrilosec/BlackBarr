#nullable enable

using System;
using System.Collections.Generic;
using System.Net.Http;
using Emby.Server.MediaEncoding.Api;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.MediaInfo;
using MediaBrowser.Model.Services;
using MediaBrowser.Model.Dlna;

namespace Emby.Plugin.BlackBarrHelper.Playback
{
    public class PlaybackInfoFilter : Attribute, IHasRequestFilter
    {
        public int Priority => -1;

        private readonly ILogger? _logger;
        private static readonly HttpClient _httpClient = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };

        public PlaybackInfoFilter()
        {
        }

        public PlaybackInfoFilter(ILogger logger)
        {
            _logger = logger;
        }

        public void RequestFilter(IRequest req, IResponse res, object requestDto)
        {
            try
            {
                if (requestDto is GetPostedPlaybackInfo postedInfo)
                {
                    _logger?.Info("[BlackBarr Helper] RequestFilter executing on GetPostedPlaybackInfo for Id={0}", postedInfo.Id);
                    ApplyTranscodeOverride(postedInfo);
                }
            }
            catch (Exception ex)
            {
                _logger?.Error("[BlackBarr Helper] Exception in RequestFilter: {0}", ex);
            }
        }

        private void ApplyTranscodeOverride(GetPostedPlaybackInfo postedInfo)
        {
            var config = Plugin.Instance?.Configuration;
            if (config == null) return;

            bool forceAll = config.ForceTranscodeAll;
            bool forceCropped = config.ForceTranscodeCropped;

            if (!forceAll && !forceCropped) return;

            bool shouldForce = forceAll;

            if (!shouldForce && forceCropped)
            {
                string itemId = postedInfo.Id;
                string? filePath = ResolveFilePath(itemId);

                if (!string.IsNullOrEmpty(filePath))
                {
                    shouldForce = CheckItemCropped(config.BlackBarrUrl, filePath!);
                }
            }

            if (shouldForce)
            {
                _logger?.Info("[BlackBarr Helper] RequestFilter ENFORCING transcode parameters on GetPostedPlaybackInfo for Id={0}", postedInfo.Id);
                postedInfo.EnableDirectPlay = false;
                postedInfo.EnableDirectStream = false;
                postedInfo.EnableTranscoding = true;
                postedInfo.AllowVideoStreamCopy = false;
                postedInfo.AllowAudioStreamCopy = true;

                if (postedInfo.DeviceProfile != null)
                {
                    postedInfo.DeviceProfile.DirectPlayProfiles = new DirectPlayProfile[0];
                    postedInfo.DeviceProfile.MaxStreamingBitrate = 140000000;
                    if (postedInfo.DeviceProfile.TranscodingProfiles != null)
                    {
                        foreach (var tp in postedInfo.DeviceProfile.TranscodingProfiles)
                        {
                            if (tp != null)
                            {
                                tp.MaxAudioChannels = "6";
                            }
                        }
                    }
                }
            }
        }

        private string? ResolveFilePath(string itemId)
        {
            if (string.IsNullOrEmpty(itemId)) return null;

            var lm = PlaybackEntryPoint.Instance?.LibraryManager;
            if (lm == null) return null;

            try
            {
                if (long.TryParse(itemId, out long longId))
                {
                    var item = lm.GetItemById(longId);
                    return item?.Path;
                }
                else if (Guid.TryParse(itemId, out Guid guidId))
                {
                    var item = lm.GetItemById(guidId);
                    return item?.Path;
                }
                else
                {
                    var item = lm.GetItemById(itemId, null);
                    return item?.Path;
                }
            }
            catch
            {
                return null;
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
