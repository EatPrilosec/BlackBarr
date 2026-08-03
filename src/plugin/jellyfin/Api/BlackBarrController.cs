#nullable enable

using System;
using System.Net.Http;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace Jellyfin.Plugin.BlackBarrHelper.Api
{
    [ApiController]
    [Route("BlackBarrHelper")]
    [Authorize(Policy = "RequiresElevation")]
    public class BlackBarrController : ControllerBase
    {
        private static readonly HttpClient _httpClient = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };

        [HttpPost("TestConnection")]
        public async Task<ActionResult> TestConnection([FromBody] TestConnectionRequest request)
        {
            if (string.IsNullOrWhiteSpace(request?.Url))
            {
                return BadRequest(new { Success = false, Message = "URL cannot be empty." });
            }

            try
            {
                var targetUrl = request.Url.TrimEnd('/') + "/health";
                var response = await _httpClient.GetAsync(targetUrl);

                if (response.IsSuccessStatusCode)
                {
                    return Ok(new { Success = true, Message = $"Successfully connected to BlackBarr API at {targetUrl}" });
                }
                else
                {
                    return Ok(new { Success = false, Message = $"BlackBarr server returned HTTP {(int)response.StatusCode} ({response.ReasonPhrase})" });
                }
            }
            catch (Exception ex)
            {
                return Ok(new { Success = false, Message = $"Jellyfin server failed to reach BlackBarr: {ex.Message}" });
            }
        }
    }

    public class TestConnectionRequest
    {
        public string? Url { get; set; }
    }
}
