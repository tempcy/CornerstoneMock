namespace CornerstoneQueue.Models;

public sealed class AppSettings
{
    public const string DefaultBridgeBaseUrl = "http://127.0.0.1:8081";

    public string BridgeBaseUrl { get; set; } = DefaultBridgeBaseUrl;

    public int StatusPollSeconds { get; set; } = 1;

    public int QueuePollSeconds { get; set; } = 5;

    public bool AlwaysOnTop { get; set; } = true;

    /// <summary>0.5–1.0，作用于主内容区。</summary>
    public double WindowOpacity { get; set; } = 1.0;

    /// <summary>文字字号缩放百分比（80–150）。</summary>
    public int FontScalePercent { get; set; } = 100;

    /// <summary>悬浮窗尺寸缩放百分比（80–150）。</summary>
    public int WindowScalePercent { get; set; } = 100;

    /// <summary>旧版合并缩放字段，仅用于读取旧配置。</summary>
    public int? UiScalePercent { get; set; }

    public bool AutoReconnect { get; set; } = true;

    public int ReconnectIntervalSeconds { get; set; } = 5;

    public AppSettings Clone() => new()
    {
        BridgeBaseUrl = BridgeBaseUrl,
        StatusPollSeconds = StatusPollSeconds,
        QueuePollSeconds = QueuePollSeconds,
        AlwaysOnTop = AlwaysOnTop,
        WindowOpacity = WindowOpacity,
        FontScalePercent = FontScalePercent,
        WindowScalePercent = WindowScalePercent,
        AutoReconnect = AutoReconnect,
        ReconnectIntervalSeconds = ReconnectIntervalSeconds,
    };

    public void Normalize()
    {
        BridgeBaseUrl = (BridgeBaseUrl ?? "").Trim().TrimEnd('/');
        if (string.IsNullOrWhiteSpace(BridgeBaseUrl))
        {
            BridgeBaseUrl = DefaultBridgeBaseUrl;
        }

        if (UiScalePercent is int legacy)
        {
            FontScalePercent = legacy;
            WindowScalePercent = legacy;
            UiScalePercent = null;
        }

        StatusPollSeconds = Math.Clamp(StatusPollSeconds, 1, 120);
        QueuePollSeconds = Math.Clamp(QueuePollSeconds, 2, 600);
        WindowOpacity = Math.Clamp(WindowOpacity, 0.5, 1.0);
        FontScalePercent = Math.Clamp(FontScalePercent, 80, 150);
        WindowScalePercent = Math.Clamp(WindowScalePercent, 80, 150);
        ReconnectIntervalSeconds = Math.Clamp(ReconnectIntervalSeconds, 2, 120);
    }
}
