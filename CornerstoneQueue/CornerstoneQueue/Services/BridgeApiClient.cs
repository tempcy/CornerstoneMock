using System.Net.Http;
using System.Text;
using System.Text.Json;
using CornerstoneQueue.Models;

namespace CornerstoneQueue.Services;

public sealed class BridgeApiClient : IDisposable
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    private readonly HttpClient _http;
    private string _baseUrl;

    public BridgeApiClient(string baseUrl, TimeSpan? timeout = null)
    {
        _baseUrl = NormalizeBaseUrl(baseUrl);
        _http = new HttpClient
        {
            Timeout = timeout ?? TimeSpan.FromSeconds(120),
        };
    }

    public string BaseUrl => _baseUrl;

    public void SetBaseUrl(string baseUrl) => _baseUrl = NormalizeBaseUrl(baseUrl);

    private static string NormalizeBaseUrl(string baseUrl)
    {
        var u = (baseUrl ?? "").Trim().TrimEnd('/');
        return string.IsNullOrWhiteSpace(u) ? AppSettings.DefaultBridgeBaseUrl : u;
    }

    public async Task<QueueListResponse?> GetQueueAsync(CancellationToken cancellationToken = default)
    {
        return await GetJsonAsync<QueueListResponse>("/api/queue", cancellationToken).ConfigureAwait(false);
    }

    public async Task<StatusResponse?> GetStatusAsync(CancellationToken cancellationToken = default)
    {
        return await GetJsonAsync<StatusResponse>("/api/status", cancellationToken).ConfigureAwait(false);
    }

    public async Task<ConfigResponse?> GetConfigAsync(CancellationToken cancellationToken = default)
    {
        return await GetJsonAsync<ConfigResponse>("/api/config", cancellationToken).ConfigureAwait(false);
    }

    public async Task<SendQueueResponse?> SendQueueAsync(
        IEnumerable<string> ids,
        CancellationToken cancellationToken = default)
    {
        var body = JsonSerializer.Serialize(new SendQueueRequest { Ids = ids.ToList() }, JsonOptions);
        using var content = new StringContent(body, Encoding.UTF8, "application/json");
        using var response = await _http
            .PostAsync($"{_baseUrl}/api/queue/send", content, cancellationToken)
            .ConfigureAwait(false);
        var text = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        if (string.IsNullOrWhiteSpace(text))
        {
            return new SendQueueResponse { Ok = false, Error = $"HTTP {(int)response.StatusCode}" };
        }

        return JsonSerializer.Deserialize<SendQueueResponse>(text, JsonOptions);
    }

    private async Task<T?> GetJsonAsync<T>(string path, CancellationToken cancellationToken)
        where T : class
    {
        using var response = await _http
            .GetAsync($"{_baseUrl}{path}", cancellationToken)
            .ConfigureAwait(false);
        var text = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        if (string.IsNullOrWhiteSpace(text))
        {
            return null;
        }

        return JsonSerializer.Deserialize<T>(text, JsonOptions);
    }

    public void Dispose() => _http.Dispose();
}
