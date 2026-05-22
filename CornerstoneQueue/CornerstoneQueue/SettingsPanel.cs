using CornerstoneQueue.Models;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CornerstoneQueue;

public sealed class SettingsPanel : UserControl
{
    private static readonly int[] ScaleChoices = [80, 90, 100, 110, 125, 150];

    private readonly TextBox _txtBridgeUrl;
    private readonly NumberBox _numStatusPoll;
    private readonly NumberBox _numQueuePoll;
    private readonly ToggleSwitch _swAlwaysOnTop;
    private readonly Slider _sliderOpacity;
    private readonly TextBlock _txtOpacityValue;
    private readonly ComboBox _cmbFontScale;
    private readonly ComboBox _cmbWindowScale;
    private readonly ToggleSwitch _swAutoReconnect;
    private readonly NumberBox _numReconnect;
    private readonly ToggleSwitch _swAutoClickUi;
    private readonly TextBox _txtWindowTitle;
    private readonly TextBox _txtNotificationId;
    private readonly TextBox _txtAddSampleId;
    private readonly NumberBox _numDelayAfterNotify;
    private readonly NumberBox _numDelayBetween;

    public SettingsPanel()
    {
        _txtBridgeUrl = new TextBox { Header = "Bridge API 地址", PlaceholderText = "http://127.0.0.1:8081" };
        _numStatusPoll = new NumberBox
        {
            Header = "状态轮询（秒）",
            Minimum = 1,
            Maximum = 120,
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline,
        };
        _numQueuePoll = new NumberBox
        {
            Header = "队列轮询（秒）",
            Minimum = 2,
            Maximum = 600,
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline,
        };
        _swAlwaysOnTop = new ToggleSwitch { Header = "悬浮窗置顶", OnContent = "开", OffContent = "关" };
        _sliderOpacity = new Slider { Header = "悬浮窗透明度", Minimum = 50, Maximum = 100, StepFrequency = 5 };
        _txtOpacityValue = new TextBlock { FontSize = 11 };
        _cmbFontScale = CreateScaleCombo("文字大小");
        _cmbWindowScale = CreateScaleCombo("窗体缩放");
        _swAutoReconnect = new ToggleSwitch { Header = "断线自动重试", OnContent = "开", OffContent = "关" };
        _numReconnect = new NumberBox
        {
            Header = "重试间隔（秒）",
            Minimum = 2,
            Maximum = 120,
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline,
        };

        _swAutoClickUi = new ToggleSwitch
        {
            Header = "发送后自动点击仪器 UI",
            OnContent = "开",
            OffContent = "关",
        };
        _txtWindowTitle = new TextBox
        {
            Header = "仪器窗口标题包含",
            PlaceholderText = "Cornerstone",
        };
        _txtNotificationId = new TextBox
        {
            Header = "消息按钮 AutomationId",
            PlaceholderText = "NotificationButton",
        };
        _txtAddSampleId = new TextBox
        {
            Header = "添加试样 AutomationId",
            PlaceholderText = "留空即可（通知面板内 Button 自动识别）",
        };
        _numDelayAfterNotify = new NumberBox
        {
            Header = "点击消息后等待（毫秒）",
            Minimum = 100,
            Maximum = 5000,
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline,
        };
        _numDelayBetween = new NumberBox
        {
            Header = "步骤间隔（毫秒）",
            Minimum = 0,
            Maximum = 5000,
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline,
        };

        var btnInspect = new Button { Content = "Inspect 检查控件", HorizontalAlignment = HorizontalAlignment.Stretch };
        btnInspect.Click += async (_, _) =>
        {
            if (InspectRequested is not null)
            {
                await InspectRequested();
            }
        };

        var btnTestClick = new Button { Content = "测试点击序列（消息 → 添加试样）", HorizontalAlignment = HorizontalAlignment.Stretch };
        btnTestClick.Click += async (_, _) =>
        {
            if (TestClickRequested is not null)
            {
                await TestClickRequested();
            }
        };

        _sliderOpacity.ValueChanged += (_, _) => UpdateOpacityLabel();
        _swAutoReconnect.Toggled += (_, _) => UpdateReconnectEnabled();
        _swAutoClickUi.Toggled += (_, _) => UpdateUiSectionEnabled();

        Content = new StackPanel
        {
            Spacing = 12,
            Children =
            {
                _txtBridgeUrl,
                _numStatusPoll,
                _numQueuePoll,
                _swAlwaysOnTop,
                _sliderOpacity,
                _txtOpacityValue,
                _cmbFontScale,
                _cmbWindowScale,
                _swAutoReconnect,
                _numReconnect,
                new TextBlock
                {
                    Text = "仪器 UI 自动点击",
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    Margin = new Thickness(0, 8, 0, 0),
                },
                _swAutoClickUi,
                _txtWindowTitle,
                _txtNotificationId,
                _txtAddSampleId,
                _numDelayAfterNotify,
                _numDelayBetween,
                btnInspect,
                btnTestClick,
            },
        };

        UpdateUiSectionEnabled();
    }

    public event Func<Task>? InspectRequested;
    public event Func<Task>? TestClickRequested;

    public void LoadFrom(AppSettings settings)
    {
        _txtBridgeUrl.Text = settings.BridgeBaseUrl;
        _numStatusPoll.Value = settings.StatusPollSeconds;
        _numQueuePoll.Value = settings.QueuePollSeconds;
        _swAlwaysOnTop.IsOn = settings.AlwaysOnTop;
        _sliderOpacity.Value = settings.WindowOpacity * 100;
        UpdateOpacityLabel();
        SelectScale(_cmbFontScale, settings.FontScalePercent);
        SelectScale(_cmbWindowScale, settings.WindowScalePercent);
        _swAutoReconnect.IsOn = settings.AutoReconnect;
        _numReconnect.Value = settings.ReconnectIntervalSeconds;
        UpdateReconnectEnabled();

        _swAutoClickUi.IsOn = settings.AutoClickInstrumentUi;
        _txtWindowTitle.Text = settings.InstrumentWindowTitleContains;
        _txtNotificationId.Text = settings.NotificationButtonAutomationId;
        _txtAddSampleId.Text = settings.AddSampleButtonAutomationId;
        _numDelayAfterNotify.Value = settings.UiClickDelayAfterNotificationMs;
        _numDelayBetween.Value = settings.UiClickDelayBetweenStepsMs;
        UpdateUiSectionEnabled();
    }

    public AppSettings ToSettings()
    {
        return new AppSettings
        {
            BridgeBaseUrl = _txtBridgeUrl.Text,
            StatusPollSeconds = (int)_numStatusPoll.Value,
            QueuePollSeconds = (int)_numQueuePoll.Value,
            AlwaysOnTop = _swAlwaysOnTop.IsOn,
            WindowOpacity = _sliderOpacity.Value / 100.0,
            FontScalePercent = ReadScale(_cmbFontScale),
            WindowScalePercent = ReadScale(_cmbWindowScale),
            AutoReconnect = _swAutoReconnect.IsOn,
            ReconnectIntervalSeconds = (int)_numReconnect.Value,
            AutoClickInstrumentUi = _swAutoClickUi.IsOn,
            InstrumentWindowTitleContains = _txtWindowTitle.Text,
            NotificationButtonAutomationId = _txtNotificationId.Text,
            AddSampleButtonAutomationId = _txtAddSampleId.Text,
            UiClickDelayAfterNotificationMs = (int)_numDelayAfterNotify.Value,
            UiClickDelayBetweenStepsMs = (int)_numDelayBetween.Value,
        };
    }

    private static ComboBox CreateScaleCombo(string header)
    {
        var box = new ComboBox { Header = header, HorizontalAlignment = HorizontalAlignment.Stretch };
        foreach (var p in ScaleChoices)
        {
            box.Items.Add(new ComboBoxItem { Content = $"{p}%", Tag = p });
        }

        return box;
    }

    private static int ReadScale(ComboBox box) =>
        box.SelectedItem is ComboBoxItem item && item.Tag is int v ? v : 100;

    private static void SelectScale(ComboBox box, int percent)
    {
        var best = 0;
        var bestDiff = int.MaxValue;
        for (var i = 0; i < ScaleChoices.Length; i++)
        {
            var diff = Math.Abs(ScaleChoices[i] - percent);
            if (diff < bestDiff)
            {
                bestDiff = diff;
                best = i;
            }
        }

        box.SelectedIndex = best;
    }

    private void UpdateOpacityLabel()
    {
        _txtOpacityValue.Text = $"当前透明度：{(int)_sliderOpacity.Value}%";
    }

    private void UpdateReconnectEnabled()
    {
        _numReconnect.IsEnabled = _swAutoReconnect.IsOn;
    }

    private void UpdateUiSectionEnabled()
    {
        var on = _swAutoClickUi.IsOn;
        _txtWindowTitle.IsEnabled = on;
        _txtNotificationId.IsEnabled = on;
        _txtAddSampleId.IsEnabled = on;
        _numDelayAfterNotify.IsEnabled = on;
        _numDelayBetween.IsEnabled = on;
    }
}
