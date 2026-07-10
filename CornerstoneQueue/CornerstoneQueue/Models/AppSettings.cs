namespace CornerstoneQueue.Models;

public sealed class AppSettings
{
    public const string DefaultBridgeBaseUrl = "http://127.0.0.1:8081";
    public const string DefaultInstrumentWindowTitleContains = "Cornerstone";
    public const string DefaultNotificationButtonAutomationId = "NotificationButton";

    public string BridgeBaseUrl { get; set; } = DefaultBridgeBaseUrl;

    public int StatusPollSeconds { get; set; } = 1;

    public int QueuePollSeconds { get; set; } = 5;

    public bool AlwaysOnTop { get; set; } = true;

    /// <summary>0.5–1.0，作用于主内容区。</summary>
    public double WindowOpacity { get; set; } = 1.0;

    /// <summary>队列列表字号（8–72，默认 24）。</summary>
    public double QueueListFontSize { get; set; } = DefaultQueueListFontSize;

    public const double DefaultQueueListFontSize = 24;
    public const double MinQueueListFontSize = 8;
    public const double MaxQueueListFontSize = 72;

    /// <summary>状态行与按钮字号缩放百分比（80–125）。</summary>
    public int UiFontScalePercent { get; set; } = 100;

    /// <summary>旧版列表缩放百分比，仅用于读取旧配置。</summary>
    public int FontScalePercent { get; set; } = 100;

    /// <summary>旧版窗体缩放百分比，仅用于读取旧配置。</summary>
    public int WindowScalePercent { get; set; } = 100;

    /// <summary>上次关闭时的窗口位置与尺寸（物理像素）；无效时启动用默认尺寸。</summary>
    public int? WindowLeft { get; set; }

    public int? WindowTop { get; set; }

    public int? WindowWidth { get; set; }

    public int? WindowHeight { get; set; }

    /// <summary>列表名称列宽（DIP）；与说明列宽同时 &gt; 0 时优先于自动列宽。</summary>
    public double QueueNameColumnWidth { get; set; }

    /// <summary>列表说明列宽（DIP）。</summary>
    public double QueueDescriptionColumnWidth { get; set; }

    /// <summary>是否显示列表列标题栏（可拖拽调整列宽）。</summary>
    public bool ShowQueueColumnHeader { get; set; }

    /// <summary>旧版合并缩放字段，仅用于读取旧配置。</summary>
    public int? UiScalePercent { get; set; }

    public bool AutoReconnect { get; set; } = true;

    public int ReconnectIntervalSeconds { get; set; } = 5;

    /// <summary>发送至仪器成功后，自动点击 Cornerstone 桌面 UI（消息 → 添加试样）。</summary>
    public bool AutoClickInstrumentUi { get; set; }

    /// <summary>仪器主窗口标题包含的字符串（如 Cornerstone）。</summary>
    public string InstrumentWindowTitleContains { get; set; } = DefaultInstrumentWindowTitleContains;

    /// <summary>消息/通知按钮 AutomationId（Inspect: NotificationButton）。</summary>
    public string NotificationButtonAutomationId { get; set; } = DefaultNotificationButtonAutomationId;

    /// <summary>添加试样按钮 AutomationId；留空则按常见 Id 与名称自动查找。</summary>
    public string AddSampleButtonAutomationId { get; set; } = "";

    /// <summary>点击消息按钮后等待毫秒（等待面板展开）。</summary>
    public int UiClickDelayAfterNotificationMs { get; set; } = 500;

    /// <summary>步骤之间的额外等待毫秒。</summary>
    public int UiClickDelayBetweenStepsMs { get; set; } = 300;

    public AppSettings Clone() => new()
    {
        BridgeBaseUrl = BridgeBaseUrl,
        StatusPollSeconds = StatusPollSeconds,
        QueuePollSeconds = QueuePollSeconds,
        AlwaysOnTop = AlwaysOnTop,
        WindowOpacity = WindowOpacity,
        QueueListFontSize = QueueListFontSize,
        UiFontScalePercent = UiFontScalePercent,
        WindowLeft = WindowLeft,
        WindowTop = WindowTop,
        WindowWidth = WindowWidth,
        WindowHeight = WindowHeight,
        QueueNameColumnWidth = QueueNameColumnWidth,
        QueueDescriptionColumnWidth = QueueDescriptionColumnWidth,
        ShowQueueColumnHeader = ShowQueueColumnHeader,
        AutoReconnect = AutoReconnect,
        ReconnectIntervalSeconds = ReconnectIntervalSeconds,
        AutoClickInstrumentUi = AutoClickInstrumentUi,
        InstrumentWindowTitleContains = InstrumentWindowTitleContains,
        NotificationButtonAutomationId = NotificationButtonAutomationId,
        AddSampleButtonAutomationId = AddSampleButtonAutomationId,
        UiClickDelayAfterNotificationMs = UiClickDelayAfterNotificationMs,
        UiClickDelayBetweenStepsMs = UiClickDelayBetweenStepsMs,
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
            QueueListFontSize = DefaultQueueListFontSize * legacy / 100.0;
            UiFontScalePercent = legacy;
            UiScalePercent = null;
        }

        if (FontScalePercent != 100)
        {
            QueueListFontSize = DefaultQueueListFontSize * FontScalePercent / 100.0;
            FontScalePercent = 100;
        }

        StatusPollSeconds = Math.Clamp(StatusPollSeconds, 1, 120);
        QueuePollSeconds = Math.Clamp(QueuePollSeconds, 2, 600);
        WindowOpacity = Math.Clamp(WindowOpacity, 0.5, 1.0);
        QueueListFontSize = Math.Clamp(QueueListFontSize, MinQueueListFontSize, MaxQueueListFontSize);
        UiFontScalePercent = Math.Clamp(UiFontScalePercent, 80, 125);
        WindowLeft = WindowLeft;
        WindowTop = WindowTop;
        WindowWidth = WindowWidth is int w and > 0 ? w : null;
        WindowHeight = WindowHeight is int h and > 0 ? h : null;
        QueueNameColumnWidth = QueueNameColumnWidth > 0 ? QueueNameColumnWidth : 0;
        QueueDescriptionColumnWidth = QueueDescriptionColumnWidth > 0 ? QueueDescriptionColumnWidth : 0;
        ReconnectIntervalSeconds = Math.Clamp(ReconnectIntervalSeconds, 2, 120);

        InstrumentWindowTitleContains = (InstrumentWindowTitleContains ?? "").Trim();
        if (string.IsNullOrWhiteSpace(InstrumentWindowTitleContains))
        {
            InstrumentWindowTitleContains = DefaultInstrumentWindowTitleContains;
        }

        NotificationButtonAutomationId = (NotificationButtonAutomationId ?? "").Trim();
        if (string.IsNullOrWhiteSpace(NotificationButtonAutomationId))
        {
            NotificationButtonAutomationId = DefaultNotificationButtonAutomationId;
        }

        AddSampleButtonAutomationId = (AddSampleButtonAutomationId ?? "").Trim();
        UiClickDelayAfterNotificationMs = Math.Clamp(UiClickDelayAfterNotificationMs, 100, 5000);
        UiClickDelayBetweenStepsMs = Math.Clamp(UiClickDelayBetweenStepsMs, 0, 5000);
    }
}
