using System.Collections.ObjectModel;
using CornerstoneQueue.Models;
using CornerstoneQueue.Services;
using CornerstoneQueue.ViewModels;
using Microsoft.UI;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;

namespace CornerstoneQueue;

public sealed partial class MainWindow : Window
{
    private const int DefaultWidth = 360;
    private const int DefaultHeight = 200;
    private const double QueueItemBaseFontSize = 24;

    private readonly BridgeApiClient _api;
    private readonly ObservableCollection<QueueItemViewModel> _items = new();
    private readonly DispatcherQueue _ui;
    private readonly DispatcherQueueTimer _statusTimer;
    private readonly DispatcherQueueTimer _queueTimer;
    private readonly DispatcherQueueTimer _reconnectTimer;

    private AppSettings _settings;
    private bool _hasWebCredentials = true;
    private bool _bridgeReachable = true;
    private bool _refreshInFlight;
    private bool _timersStarted;
    private string _lastQueueFingerprint = "";
    private string _lastStatusLine = "";
    private string _lastResultLine = "";

    private readonly EdgeDockController _edgeDock;
    private SettingsWindow? _settingsWindow;

    public MainWindow()
    {
        _settings = AppSettingsStore.Load();
        _api = new BridgeApiClient(_settings.BridgeBaseUrl);

        InitializeComponent();
        _edgeDock = new EdgeDockController(this, DockRoot);
        SystemSnapDisabler.Attach(this);
        Closed += (_, _) =>
        {
            _edgeDock.Dispose();
            SystemSnapDisabler.Detach();
            _api.Dispose();
        };

        QueueList.ItemsSource = _items;

        _ui = DispatcherQueue.GetForCurrentThread();
        _statusTimer = _ui.CreateTimer();
        _statusTimer.Tick += async (_, _) => await PollStatusAsync();

        _queueTimer = _ui.CreateTimer();
        _queueTimer.Tick += async (_, _) => await RefreshQueueAsync(silent: true);

        _reconnectTimer = _ui.CreateTimer();
        _reconnectTimer.Tick += async (_, _) => await ReconnectTickAsync();

        Activated += OnWindowActivated;

        ApplySettings(initialize: true);
    }

    private void OnSettingsClick(object sender, RoutedEventArgs e)
    {
        if (_settingsWindow is not null)
        {
            _settingsWindow.Activate();
            return;
        }

        _settingsWindow = new SettingsWindow(_settings.Clone(), OnSettingsWindowClosed);
        _settingsWindow.Closed += (_, _) => _settingsWindow = null;
        _settingsWindow.Activate();
    }

    private async void OnSettingsWindowClosed(AppSettings? saved)
    {
        _settingsWindow = null;
        if (saved is null)
        {
            return;
        }

        _settings = saved;
        AppSettingsStore.Save(_settings);
        ApplySettings(initialize: false);
        ShowSendResult("设置已保存", isError: false);
        await RefreshAllAsync();
    }

    private void ApplySettings(bool initialize)
    {
        _settings.Normalize();
        _api.SetBaseUrl(_settings.BridgeBaseUrl);

        _statusTimer.Interval = TimeSpan.FromSeconds(_settings.StatusPollSeconds);
        _queueTimer.Interval = TimeSpan.FromSeconds(_settings.QueuePollSeconds);
        _reconnectTimer.Interval = TimeSpan.FromSeconds(_settings.ReconnectIntervalSeconds);

        SetAlwaysOnTop(_settings.AlwaysOnTop);
        DockRoot.Opacity = _settings.WindowOpacity;

        var fontScale = _settings.FontScalePercent / 100.0;
        var statusFont = 11 * fontScale;
        var bodyFont = 12 * fontScale;
        TxtStatusLine.FontSize = statusFont;
        TxtResultLine.FontSize = statusFont;
        BtnRefresh.FontSize = bodyFont;
        BtnSettings.FontSize = bodyFont;
        BtnSend.FontSize = bodyFont;
        QueueList.FontSize = QueueItemBaseFontSize * fontScale;

        var winScale = _settings.WindowScalePercent / 100.0;
        var w = Math.Max(280, (int)Math.Round(DefaultWidth * winScale));
        var h = Math.Max(160, (int)Math.Round(DefaultHeight * winScale));
        AppWindow.Resize(new SizeInt32(w, h));

        UpdateReconnectTimerState();

        if (!initialize && _timersStarted)
        {
            _ = RefreshAllAsync();
        }
    }

    private void SetAlwaysOnTop(bool onTop)
    {
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.IsAlwaysOnTop = onTop;
        }

        _edgeDock.SyncAlwaysOnTop(onTop);
    }

    private void RunOnUi(Action action)
    {
        if (_ui.HasThreadAccess)
        {
            action();
        }
        else
        {
            _ui.TryEnqueue(() => action());
        }
    }

    private async void OnWindowActivated(object sender, WindowActivatedEventArgs e)
    {
        if (_timersStarted)
        {
            return;
        }

        _timersStarted = true;
        _statusTimer.Start();
        _queueTimer.Start();
        await RefreshAllAsync();
    }

    private async void OnRefreshClick(object sender, RoutedEventArgs e)
    {
        BtnRefresh.IsEnabled = false;
        try
        {
            await RefreshAllAsync();
        }
        finally
        {
            BtnRefresh.IsEnabled = true;
        }
    }

    private async void OnSendClick(object sender, RoutedEventArgs e)
    {
        var ids = QueueList.SelectedItems
            .OfType<QueueItemViewModel>()
            .Select(i => i.Id)
            .Where(id => !string.IsNullOrWhiteSpace(id))
            .ToList();

        if (ids.Count == 0)
        {
            ShowSendResult("请先勾选条目（Ctrl+单击多选）", isError: true);
            return;
        }

        BtnSend.IsEnabled = false;
        try
        {
            SendQueueResponse? data;
            try
            {
                data = await _api.SendQueueAsync(ids);
            }
            catch (Exception ex)
            {
                ShowSendResult($"请求失败：{ex.Message}", isError: true);
                MarkBridgeOffline(ex.Message);
                return;
            }

            ShowSendResult(
                SendResultFormatter.FormatOneLine(data, _hasWebCredentials),
                isError: data is not { Ok: true });
            await RefreshQueueAsync(silent: true, force: true);
            await PollStatusAsync();
        }
        finally
        {
            BtnSend.IsEnabled = true;
        }
    }

    private async Task RefreshAllAsync()
    {
        await PollStatusAsync();
        if (_bridgeReachable)
        {
            await RefreshQueueAsync(silent: false, force: true);
        }

        try
        {
            var cfg = await _api.GetConfigAsync();
            if (cfg != null)
            {
                _hasWebCredentials = cfg.HasWebCredentials;
                if (!_hasWebCredentials && _bridgeReachable)
                {
                    RunOnUi(() =>
                        ApplyStatusLine(
                            $"Bridge {_api.BaseUrl} · 未配置 web_user/web_password，发送可能失败",
                            bridgeOk: false));
                }
            }
        }
        catch
        {
            // 配置拉取失败不阻断只读队列
        }
    }

    private async Task PollStatusAsync()
    {
        try
        {
            var data = await _api.GetStatusAsync();
            RunOnUi(() =>
            {
                if (!_bridgeReachable)
                {
                    ShowSendResult("Bridge 已重新连接", isError: false);
                }

                MarkBridgeOnline();
                ApplyStatus(data, bridgeReachable: true);
            });
        }
        catch (Exception ex)
        {
            RunOnUi(() =>
            {
                MarkBridgeOffline(ex.Message);
                ApplyStatus(null, bridgeReachable: false, error: ex.Message);
            });
        }
    }

    private async Task ReconnectTickAsync()
    {
        if (_bridgeReachable || !_settings.AutoReconnect)
        {
            return;
        }

        await PollStatusAsync();
        if (_bridgeReachable)
        {
            await RefreshQueueAsync(silent: true, force: true);
        }
    }

    private void MarkBridgeOnline()
    {
        _bridgeReachable = true;
        UpdateReconnectTimerState();
    }

    private void MarkBridgeOffline(string? error)
    {
        var wasOnline = _bridgeReachable;
        _bridgeReachable = false;
        UpdateReconnectTimerState();
        if (wasOnline && _settings.AutoReconnect)
        {
            ShowSendResult(
                $"Bridge 不可达，{ _settings.ReconnectIntervalSeconds } 秒后重试…",
                isError: true);
        }
    }

    private void UpdateReconnectTimerState()
    {
        if (_bridgeReachable || !_settings.AutoReconnect)
        {
            _reconnectTimer.Stop();
            return;
        }

        if (!_reconnectTimer.IsRunning)
        {
            _reconnectTimer.Interval = TimeSpan.FromSeconds(_settings.ReconnectIntervalSeconds);
            _reconnectTimer.Start();
        }
    }

    private async Task RefreshQueueAsync(bool silent, bool force = false)
    {
        if (_refreshInFlight || !_bridgeReachable)
        {
            return;
        }

        _refreshInFlight = true;
        try
        {
            QueueListResponse? data;
            try
            {
                data = await _api.GetQueueAsync();
            }
            catch (Exception ex)
            {
                if (!silent)
                {
                    RunOnUi(() =>
                        ApplyStatusLine($"Bridge · 队列加载失败：{ex.Message}", bridgeOk: false));
                }

                MarkBridgeOffline(ex.Message);
                return;
            }

            RunOnUi(() => ApplyQueueData(data, silent, force));
        }
        finally
        {
            _refreshInFlight = false;
        }
    }

    private void ApplyQueueData(QueueListResponse? data, bool silent, bool force)
    {
        if (data is not { Ok: true })
        {
            if (!silent)
            {
                ApplyStatusLine($"Bridge · {data?.Error ?? "加载队列失败"}", bridgeOk: false);
            }

            return;
        }

        var items = (data.Items ?? new List<QueueItemDto>())
            .OrderByDescending(i => i.ReceivedAt)
            .ThenByDescending(i => i.Id, StringComparer.Ordinal)
            .ToList();
        var fingerprint = QueueItemViewModel.Fingerprint(items);
        if (!force && fingerprint == _lastQueueFingerprint)
        {
            return;
        }

        _lastQueueFingerprint = fingerprint;

        var selected = QueueList.SelectedItems
            .OfType<QueueItemViewModel>()
            .Select(i => i.Id)
            .ToHashSet(StringComparer.Ordinal);

        _items.Clear();
        foreach (var dto in items)
        {
            var vm = new QueueItemViewModel(dto);
            _items.Add(vm);
            if (selected.Contains(vm.Id))
            {
                QueueList.SelectedItems.Add(vm);
            }
        }
    }

    private void ApplyStatus(StatusResponse? data, bool bridgeReachable, string? error = null)
    {
        if (!bridgeReachable)
        {
            ApplyStatusLine(
                $"Bridge {_api.BaseUrl} · 不可达{(string.IsNullOrWhiteSpace(error) ? "" : " · " + error)}",
                bridgeOk: false);
            return;
        }

        if (data is not { Ok: true })
        {
            ApplyStatusLine($"Bridge · {data?.Error ?? "状态未知"}", bridgeOk: false);
            return;
        }

        var upstream = data.UpstreamConnected ? "上游已连接" : "上游未连接";
        var rcs = string.IsNullOrWhiteSpace(data.RemoteControlState) ? "—" : data.RemoteControlState;
        var line =
            $"Bridge · {upstream} · 队列 {data.QueueCount}/{data.QueueMax} · RCS {rcs}";
        if (!string.IsNullOrWhiteSpace(data.RemoteControlStateError))
        {
            line += $" ({data.RemoteControlStateError})";
        }

        ApplyStatusLine(line, bridgeOk: true);
    }

    private void ApplyStatusLine(string line, bool bridgeOk)
    {
        if (line == _lastStatusLine)
        {
            return;
        }

        _lastStatusLine = line;
        TxtStatusLine.Text = line;
        DotBridge.Fill = new SolidColorBrush(bridgeOk ? Colors.LimeGreen : Colors.OrangeRed);
    }

    private void ShowSendResult(string text, bool isError)
    {
        var oneLine = (text ?? "").Replace('\r', ' ').Replace('\n', ' ').Trim();
        if (oneLine == _lastResultLine && TxtResultLine.Visibility == Visibility.Visible)
        {
            return;
        }

        _lastResultLine = oneLine;
        if (string.IsNullOrWhiteSpace(oneLine))
        {
            TxtResultLine.Visibility = Visibility.Collapsed;
            TxtResultLine.Text = "";
            return;
        }

        TxtResultLine.Text = oneLine;
        TxtResultLine.Visibility = Visibility.Visible;
        TxtResultLine.Foreground = new SolidColorBrush(isError ? Colors.OrangeRed : Colors.Green);
    }
}
