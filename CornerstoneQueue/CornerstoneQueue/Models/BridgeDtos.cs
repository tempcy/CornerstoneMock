using System.Text.Json.Serialization;

namespace CornerstoneQueue.Models;

public sealed class QueueListResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("error")]
    public string? Error { get; set; }

    [JsonPropertyName("items")]
    public List<QueueItemDto>? Items { get; set; }
}

public sealed class QueueItemDto
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("sampleName")]
    public string? SampleName { get; set; }

    [JsonPropertyName("sampleDescription")]
    public string? SampleDescription { get; set; }

    [JsonPropertyName("receivedAt")]
    public double ReceivedAt { get; set; }

    [JsonPropertyName("receivedAtText")]
    public string? ReceivedAtText { get; set; }

    [JsonPropertyName("peer")]
    public string? Peer { get; set; }

    [JsonPropertyName("xml")]
    public string? Xml { get; set; }
}

public sealed class StatusResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("error")]
    public string? Error { get; set; }

    [JsonPropertyName("upstreamConnected")]
    public bool UpstreamConnected { get; set; }

    [JsonPropertyName("queueCount")]
    public int QueueCount { get; set; }

    [JsonPropertyName("queueMax")]
    public int QueueMax { get; set; }

    [JsonPropertyName("remoteControlState")]
    public string? RemoteControlState { get; set; }

    [JsonPropertyName("remoteControlStateError")]
    public string? RemoteControlStateError { get; set; }
}

public sealed class ConfigResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("hasWebCredentials")]
    public bool HasWebCredentials { get; set; }
}

public sealed class SendQueueRequest
{
    [JsonPropertyName("ids")]
    public List<string> Ids { get; set; } = new();
}

public sealed class SendQueueResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("error")]
    public string? Error { get; set; }

    [JsonPropertyName("queueKept")]
    public bool QueueKept { get; set; }

    [JsonPropertyName("results")]
    public List<SendQueueResultItem>? Results { get; set; }
}

public sealed class SendQueueResultItem
{
    [JsonPropertyName("id")]
    public string? Id { get; set; }

    [JsonPropertyName("upstreamResponse")]
    public string? UpstreamResponse { get; set; }
}
