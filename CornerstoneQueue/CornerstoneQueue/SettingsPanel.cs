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

        _sliderOpacity.ValueChanged += (_, _) => UpdateOpacityLabel();
        _swAutoReconnect.Toggled += (_, _) => UpdateReconnectEnabled();

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
            },
        };
    }

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
}
