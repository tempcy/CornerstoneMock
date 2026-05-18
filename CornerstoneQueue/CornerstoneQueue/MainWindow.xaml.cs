using System.Collections.ObjectModel;
using CornerstoneQueue.Models;
using CornerstoneQueue.Services;
using CornerstoneQueue.ViewModels;
using Microsoft.UI;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;

namespace CornerstoneQueue;

public sealed partial class MainWindow : Window
{
    public const string DefaultBridgeBaseUrl = "http://127.0.0.1:8081";

    private static readonly TimeSpan StatusPollInterval = TimeSpan.FromSeconds(1);
    private static readonly TimeSpan QueuePollInterval = TimeSpan.FromSeconds(5);

    private const int DefaultWidth = 360;
    private const int DefaultHeight = 200;

    private readonly BridgeApiClient _api = new(DefaultBridgeBaseUrl);
    private readonly ObservableCollection<QueueItemViewModel> _items = new();
    private readonly DispatcherQueue _ui;
    private readonly DispatcherQueueTimer _statusTimer;
    private readonly DispatcherQueueTimer _queueTimer;

    private bool _hasWebCredentials = true;
    private bool _refreshInFlight;
    private bool _timersStarted;
    private string _lastQueueFingerprint = "";
    private string _lastStatusLine = "";
    private string _lastResultLine = "";

    public MainWindow()
    {
        InitializeComponent();
        AppWindow.Resize(new SizeInt32(DefaultWidth, DefaultHeight));

        QueueList.ItemsSource = _items;

        _ui = DispatcherQueue.GetForCurrentThread();
        _statusTimer = _ui.CreateTimer();
        _statusTimer.Interval = StatusPollInterval;
        _statusTimer.Tick += async (_, _) => await PollStatusAsync();

        _queueTimer = _ui.CreateTimer();
        _queueTimer.Interval = QueuePollInterval;
        _queueTimer.Tick += async (_, _) => await RefreshQueueAsync(silent: true);

        Activated += OnWindowActivated;
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
        await RefreshQueueAsync(silent: false, force: true);
        try
        {
            var cfg = await _api.GetConfigAsync();
            if (cfg != null)
            {
                _hasWebCredentials = cfg.HasWebCredentials;
                if (!_hasWebCredentials)
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
            RunOnUi(() => ApplyStatus(data, bridgeReachable: true));
        }
        catch (Exception ex)
        {
            RunOnUi(() => ApplyStatus(null, bridgeReachable: false, error: ex.Message));
        }
    }

    private async Task RefreshQueueAsync(bool silent, bool force = false)
    {
        if (_refreshInFlight)
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

        var items = data.Items ?? new List<QueueItemDto>();
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
