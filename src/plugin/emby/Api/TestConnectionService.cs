#nullable enable

using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Threading.Tasks;
using MediaBrowser.Model.Services;

namespace Emby.Plugin.BlackBarrHelper.Api
{
    [Route("/BlackBarr/TestConnection", "GET", Summary = "Tests connection to BlackBarr API server")]
    public class TestConnectionRequest : IReturn<TestConnectionResponse>
    {
        [ApiMember(Name = "Url", Description = "BlackBarr API server URL", IsRequired = false, DataType = "string", ParameterType = "query")]
        public string? Url { get; set; }
    }

    public class TestConnectionResponse
    {
        public bool Success { get; set; }
        public string? Message { get; set; }
        public string? TestedUrl { get; set; }
    }

    public class TestConnectionService : IService
    {
        private static readonly HttpClient _httpClient = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };

        public object Get(TestConnectionRequest request)
        {
            return GetAsync(request).GetAwaiter().GetResult();
        }

        private async Task<TestConnectionResponse> GetAsync(TestConnectionRequest request)
        {
            var userUrl = request.Url;
            var candidates = new List<string>();
            if (!string.IsNullOrWhiteSpace(userUrl)) candidates.Add(userUrl);

            var savedUrl = Plugin.Instance?.Configuration?.BlackBarrUrl;
            if (!string.IsNullOrWhiteSpace(savedUrl) && !candidates.Contains(savedUrl)) candidates.Add(savedUrl);

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
                        return new TestConnectionResponse
                        {
                            Success = true,
                            Message = $"Successfully connected to BlackBarr server at {healthUrl}",
                            TestedUrl = url
                        };
                    }
                    else
                    {
                        lastError = $"Server at {healthUrl} returned HTTP {(int)resp.StatusCode} ({resp.ReasonPhrase})";
                    }
                }
                catch (Exception ex)
                {
                    lastError = $"Failed to reach {healthUrl}: {ex.Message}";
                }
            }

            return new TestConnectionResponse
            {
                Success = false,
                Message = lastError
            };
        }
    }
}
