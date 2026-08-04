#nullable enable

using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
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
        private static readonly HttpClient _httpClient = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };

        public PlaybackInfoMiddleware(ILogger<PlaybackInfoMiddleware> logger)
        {
            _logger = logger;
        }

        public async Task InvokeAsync(HttpContext context, RequestDelegate next)
        {
            var path = context.Request.Path.Value ?? string.Empty;
            var query = context.Request.QueryString.Value ?? string.Empty;

            if (path.Contains("BlackBarr", StringComparison.OrdinalIgnoreCase) || path.Contains("TestConnection", StringComparison.OrdinalIgnoreCase) || query.Contains("TestConnection", StringComparison.OrdinalIgnoreCase))
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

            // Remove Accept-Encoding from request so Jellyfin inner pipeline produces raw JSON when possible
            var clientEncoding = context.Request.Headers["Accept-Encoding"].ToString();
            context.Request.Headers.Remove("Accept-Encoding");

            // Mutate incoming request payload to force Jellyfin to generate a TranscodingUrl
            if (context.Request.Method.Equals("POST", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    context.Request.EnableBuffering();
                    using var reqReader = new StreamReader(context.Request.Body, Encoding.UTF8, leaveOpen: true);
                    string reqJsonString = await reqReader.ReadToEndAsync();
                    context.Request.Body.Seek(0, SeekOrigin.Begin);

                    if (!string.IsNullOrEmpty(reqJsonString))
                    {
                        var reqDoc = JsonNode.Parse(reqJsonString);
                        if (reqDoc != null)
                        {
                            reqDoc["EnableDirectPlay"] = false;
                            reqDoc["EnableDirectStream"] = false;
                            reqDoc["EnableTranscoding"] = true;
                            reqDoc["AllowVideoStreamCopy"] = false;
                            reqDoc["AllowAudioStreamCopy"] = true;
                            reqDoc["MaxStreamingBitrate"] = 140000000;

                            if (reqDoc["DeviceProfile"] is JsonObject dp)
                            {
                                dp["DirectPlayProfiles"] = new JsonArray();
                                dp["MaxStreamingBitrate"] = 140000000;
                                dp["MaxStaticBitrate"] = 140000000;
                                if (dp["TranscodingProfiles"] is JsonArray tps)
                                {
                                    foreach (var tp in tps)
                                    {
                                        if (tp != null)
                                        {
                                            tp["MaxAudioChannels"] = "6";
                                            if (tp["MaxStreamingBitrate"] != null)
                                                tp["MaxStreamingBitrate"] = "140000000";
                                        }
                                    }
                                }
                            }

                            string mutatedReqJson = reqDoc.ToJsonString();
                            byte[] mutatedReqBytes = Encoding.UTF8.GetBytes(mutatedReqJson);
                            var newReqStream = new MemoryStream();
                            await newReqStream.WriteAsync(mutatedReqBytes, 0, mutatedReqBytes.Length);
                            newReqStream.Seek(0, SeekOrigin.Begin);

                            context.Request.Body = newReqStream;
                            context.Request.ContentLength = mutatedReqBytes.Length;
                        }
                    }
                }
                catch (Exception ex)
                {
                    _logger.LogWarning(ex, "[BlackBarr Helper] Failed to mutate PlaybackInfo incoming request payload");
                }
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

            string contentEncoding = context.Response.Headers.ContentEncoding.ToString();
            string jsonString = string.Empty;

            try
            {
                Stream decompressedStream = responseBody;
                if (contentEncoding.Contains("gzip", StringComparison.OrdinalIgnoreCase))
                {
                    decompressedStream = new GZipStream(responseBody, CompressionMode.Decompress, leaveOpen: true);
                }
                else if (contentEncoding.Contains("br", StringComparison.OrdinalIgnoreCase))
                {
                    decompressedStream = new BrotliStream(responseBody, CompressionMode.Decompress, leaveOpen: true);
                }

                using (var reader = new StreamReader(decompressedStream, Encoding.UTF8, leaveOpen: true))
                {
                    jsonString = await reader.ReadToEndAsync();
                }

                // Strip UTF-8 BOM if present
                if (jsonString.StartsWith('\uFEFF'))
                {
                    jsonString = jsonString.Substring(1);
                }
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "[BlackBarr Helper] Failed to read response stream on PlaybackInfo");
                responseBody.Seek(0, SeekOrigin.Begin);
                await responseBody.CopyToAsync(originalBodyStream);
                return;
            }

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

            byte[] finalBytes = Encoding.UTF8.GetBytes(jsonString);

            // Re-compress response if client requested gzip/brotli
            if (clientEncoding.Contains("gzip", StringComparison.OrdinalIgnoreCase))
            {
                using (var outMs = new MemoryStream())
                {
                    await using (var gzipMs = new GZipStream(outMs, CompressionMode.Compress, leaveOpen: true))
                    {
                        await gzipMs.WriteAsync(finalBytes, 0, finalBytes.Length);
                    }
                    finalBytes = outMs.ToArray();
                }
                context.Response.Headers["Content-Encoding"] = "gzip";
            }
            else if (clientEncoding.Contains("br", StringComparison.OrdinalIgnoreCase))
            {
                using (var outMs = new MemoryStream())
                {
                    await using (var brotliMs = new BrotliStream(outMs, CompressionLevel.Fastest, leaveOpen: true))
                    {
                        await brotliMs.WriteAsync(finalBytes, 0, finalBytes.Length);
                    }
                    finalBytes = outMs.ToArray();
                }
                context.Response.Headers["Content-Encoding"] = "br";
            }
            
            context.Response.Headers.Remove("Transfer-Encoding");
            context.Response.ContentLength = finalBytes.Length;
            await originalBodyStream.WriteAsync(finalBytes, 0, finalBytes.Length);
        }

        private async Task HandleTestConnectionAsync(HttpContext context)
        {
            if (!context.Response.HasStarted)
            {
                context.Response.Clear();
                context.Response.StatusCode = 200;
                context.Response.ContentType = "application/json; charset=utf-8";
            }

            string userUrl = context.Request.Query["Url"].ToString();

            if (string.IsNullOrWhiteSpace(userUrl))
            {
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
                        userUrl = reqDoc?["Url"]?.ToString() ?? string.Empty;
                    }
                }
                catch
                {
                    // Ignore body parse errors
                }
            }

            var candidates = new List<string>();
            if (!string.IsNullOrWhiteSpace(userUrl))
            {
                candidates.Add(userUrl);
            }
            var savedUrl = Plugin.Instance?.Configuration?.BlackBarrUrl;
            if (!string.IsNullOrWhiteSpace(savedUrl) && !candidates.Contains(savedUrl))
            {
                candidates.Add(savedUrl);
            }
            if (!candidates.Contains("http://127.0.0.1:6795")) candidates.Add("http://127.0.0.1:6795");
            if (!candidates.Contains("http://blackbarr:6795")) candidates.Add("http://blackbarr:6795");
            if (!candidates.Contains("http://localhost:6795")) candidates.Add("http://localhost:6795");

            string lastError = "No candidate URLs responded.";
            foreach (var url in candidates)
            {
                var healthUrl = url.TrimEnd('/') + "/health";
                try
                {
                    var resp = await _httpClient.GetAsync(healthUrl);
                    if (resp.IsSuccessStatusCode)
                    {
                        var resJson = $"{{\"Success\":true,\"Message\":\"Successfully connected to BlackBarr server at {healthUrl}\",\"TestedUrl\":\"{url.Replace("\"", "'")}\"}}";
                        await context.Response.WriteAsync(resJson);
                        return;
                    }
                    else
                    {
                        lastError = $"Server at {healthUrl} returned HTTP {(int)resp.StatusCode} ({resp.ReasonPhrase})";
                    }
                }
                catch (Exception ex)
                {
                    lastError = $"Failed to reach {healthUrl}: {ex.Message.Replace("\"", "'")}";
                }
            }

            var failJson = $"{{\"Success\":false,\"Message\":\"{lastError.Replace("\"", "'")}\"}}";
            await context.Response.WriteAsync(failJson);
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
