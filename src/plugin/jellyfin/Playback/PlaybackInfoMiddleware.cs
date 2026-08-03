#nullable enable

using System;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Jellyfin.Plugin.BlackBarrHelper.Configuration;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.BlackBarrHelper.Playback
{
    public class PlaybackInfoMiddleware : IMiddleware
    {
        private readonly ILogger<PlaybackInfoMiddleware> _logger;
        private static readonly HttpClient _httpClient = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };

        public PlaybackInfoMiddleware(ILogger<PlaybackInfoMiddleware> logger)
        {
            _logger = logger;
        }

        public async Task InvokeAsync(HttpContext context, RequestDelegate next)
        {
            var path = context.Request.Path.Value ?? string.Empty;

            if (path.Contains("TestConnection", StringComparison.OrdinalIgnoreCase) || path.Contains("blackbarr", StringComparison.OrdinalIgnoreCase))
            {
                _logger.LogInformation("[BlackBarr Helper] Intercepted test connection request at path: {Path}", path);
                await HandleTestConnectionAsync(context);
                return;
            }

            if (!path.Contains("PlaybackInfo", StringComparison.OrdinalIgnoreCase))
            {
                await next(context);
                return;
            }

            var config = Plugin.Instance?.Configuration ?? new PluginConfiguration();
            bool forceAll = config.ForceTranscodeAll;
            bool forceCropped = config.ForceTranscodeCropped;

            if (!forceAll && !forceCropped)
            {
                await next(context);
                return;
            }

            var originalBodyStream = context.Response.Body;
            using var responseBody = new MemoryStream();
            context.Response.Body = responseBody;

            await next(context);

            context.Response.Body = originalBodyStream;
            responseBody.Seek(0, SeekOrigin.Begin);

            if (context.Response.StatusCode != 200)
            {
                await responseBody.CopyToAsync(originalBodyStream);
                return;
            }

            string jsonString = await new StreamReader(responseBody).ReadToEndAsync();
            try
            {
                var doc = JsonNode.Parse(jsonString);
                if (doc != null && doc["MediaSources"] is JsonArray mediaSources)
                {
                    bool shouldForce = forceAll;

                    if (!shouldForce && forceCropped)
                    {
                        foreach (var ms in mediaSources)
                        {
                            string? filePath = ms?["Path"]?.ToString();
                            if (!string.IsNullOrEmpty(filePath) && await CheckItemCroppedAsync(config.BlackBarrUrl, filePath))
                            {
                                shouldForce = true;
                                break;
                            }
                        }
                    }

                    if (shouldForce)
                    {
                        _logger.LogInformation("[BlackBarr Helper] Enforcing Transcode mode in PlaybackInfo response");
                        foreach (var ms in mediaSources)
                        {
                            if (ms == null) continue;
                            ms["SupportsDirectPlay"] = false;
                            ms["SupportsDirectStream"] = false;
                            ms["SupportsTranscoding"] = true;
                            ms["SupportsSubtitlesInHls"] = false;
                            ms["PlayMethod"] = "Transcode";
                            ms["DirectStreamUrl"] = null;

                            var tUrl = ms["TranscodingUrl"]?.ToString();
                            if (!string.IsNullOrEmpty(tUrl))
                            {
                                tUrl = Regex.Replace(tUrl, @"((?:MaxStreamingBitrate|VideoBitrate|MaxBitrate)=)\d+", "${1}140000000", RegexOptions.IgnoreCase);
                                tUrl = Regex.Replace(tUrl, @"SubtitleMethod=Hls", "SubtitleMethod=External", RegexOptions.IgnoreCase);
                                ms["TranscodingUrl"] = tUrl;
                            }
                        }

                        jsonString = doc.ToJsonString();
                    }
                }
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "[BlackBarr Helper] Failed to parse or mutate PlaybackInfo response JSON");
            }

            var modifiedBytes = Encoding.UTF8.GetBytes(jsonString);
            context.Response.ContentLength = modifiedBytes.Length;
            await originalBodyStream.WriteAsync(modifiedBytes, 0, modifiedBytes.Length);
        }

        private async Task HandleTestConnectionAsync(HttpContext context)
        {
            context.Response.StatusCode = 200;
            context.Response.ContentType = "application/json";

            string targetUrl = string.Empty;

            try
            {
                context.Request.EnableBuffering();
                context.Request.Body.Position = 0;
                using var reader = new StreamReader(context.Request.Body, Encoding.UTF8, leaveOpen: true);
                string reqBody = await reader.ReadToEndAsync();
                context.Request.Body.Position = 0;

                if (!string.IsNullOrWhiteSpace(reqBody))
                {
                    var reqDoc = JsonNode.Parse(reqBody);
                    targetUrl = reqDoc?["Url"]?.ToString() ?? string.Empty;
                }
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "[BlackBarr Helper] Failed to parse TestConnection request body");
            }

            if (string.IsNullOrWhiteSpace(targetUrl))
            {
                targetUrl = Plugin.Instance?.Configuration?.BlackBarrUrl ?? "http://127.0.0.1:6795";
            }

            var healthUrl = targetUrl.TrimEnd('/') + "/health";
            try
            {
                var resp = await _httpClient.GetAsync(healthUrl);
                string resJson;
                if (resp.IsSuccessStatusCode)
                {
                    resJson = $"{{\"Success\":true,\"Message\":\"Successfully connected to BlackBarr server at {healthUrl}\"}}";
                }
                else
                {
                    resJson = $"{{\"Success\":false,\"Message\":\"BlackBarr server returned HTTP {(int)resp.StatusCode} ({resp.ReasonPhrase}) at {healthUrl}\"}}";
                }
                await context.Response.WriteAsync(resJson);
            }
            catch (Exception ex)
            {
                var resJson = $"{{\"Success\":false,\"Message\":\"Jellyfin server failed to reach BlackBarr at {healthUrl}: {ex.Message.Replace("\"", "'")}\"}}";
                await context.Response.WriteAsync(resJson);
            }
        }

        private async Task<bool> CheckItemCroppedAsync(string blackbarrUrl, string filePath)
        {
            try
            {
                var requestUrl = $"{blackbarrUrl.TrimEnd('/')}/api/crop_val?path={Uri.EscapeDataString(filePath)}";
                var resp = await _httpClient.GetAsync(requestUrl);
                if (resp.IsSuccessStatusCode)
                {
                    var content = await resp.Content.ReadAsStringAsync();
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
