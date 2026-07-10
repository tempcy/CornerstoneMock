using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using CornerstoneQueue.Models;
using CornerstoneQueue.Services;
using CornerstoneQueue.ViewModels;
using Microsoft.UI;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;
using WinRT.Interop;

namespace CornerstoneQueue;

public sealed partial class MainWindow : Window, INotifyPropertyChanged
{
    private const int DefaultWidth = 360;
    private const int DefaultHeight = 200;
    private const double QueueNameColumnMinDip = 48;
    private const double QueueDescColumnMinDip = 48;
    private const double QueueColumnSplitterDip = 12;
    private const double QueueColumnMeasurePadDip = 8;

    private readonly BridgeApiClient _api;
    private readonly ObservableCollection<QueueItemViewModel> _items = new();
    private readonly DispatcherQueue _ui;
    private readonly DispatcherQueueTimer _statusTimer;
    private readonly DispatcherQueueTimer _queueTimer;
    private readonly DispatcherQueueTimer _reconnectTimer;
    private readonly DispatcherQueueTimer _windowBoundsTimer;
    private readonly DispatcherQueueTimer _columnSaveTimer;
    private readonly IntPtr _hwnd;

    private AppSettings _settings;
    private bool _hasWebCredentials = true;
    private bool _bridgeReachable = true;
    private bool _refreshInFlight;
    private bool _timersStarted;
    private bool _suppressWindowSave;
    private string _lastQueueFingerprint = "";
    private string _lastStatusLine = "";
    private string _lastResultLine = "";

    private readonly EdgeDockController _edgeDock;
    private readonly InstrumentUiAutomationService _uiAutomation = new();
    private SettingsWindow? _settingsWindow;
    private double _queueNameColumnWidth = 120;
    private double _queueDescriptionColumnWidth = 120;
    private GridLength _queueNameColumnLength = new(120);
    private GridLength _queueDescriptionColumnLength = new(120);
    private double _queueListFontSize = AppSettings.DefaultQueueListFontSize;
    private bool _columnSplitterDragging;
    private bool _columnSplitterPointerDown;
    private const double ColumnDragThresholdDip = 3;
    private double _columnDragStartX;
    private double _columnDragStartNameW;
    private uint _columnDragPointerId;
    private Pointer? _columnDragPointer;

    public event PropertyChangedEventHandler? PropertyChanged;

    public double QueueListFontSize
    {
        get => _queueListFontSize;
        private set
        {
            if (Math.Abs(_queueListFontSize - value) < 0.01)
            {
                return;
            }

            _queueListFontSize = value;
            OnPropertyChanged();
        }
    }

    public GridLength QueueNameColumnLength
    {
        get => _queueNameColumnLength;
        private set
        {
            if (Math.Abs(_queueNameColumnLength.Value - value.Value) < 0.5)
            {
                return;
            }

            _queueNameColumnLength = value;
            OnPropertyChanged();
        }
    }

    public GridLength QueueDescriptionColumnLength
    {
        get => _queueDescriptionColumnLength;
        private set
        {
            if (Math.Abs(_queueDescriptionColumnLength.Value - value.Value) < 0.5)
            {
                return;
            }

            _queueDescriptionColumnLength = value;
            OnPropertyChanged();
        }
    }

    public double QueueNameColumnWidth
    {
        get => _queueNameColumnWidth;
        private set
        {
            if (Math.Abs(_queueNameColumnWidth - value) < 0.5)
            {
                return;
            }

            _queueNameColumnWidth = value;
            OnPropertyChanged();
        }
    }

    public double QueueDescriptionColumnWidth
    {
        get => _queueDescriptionColumnWidth;
        private set
        {
            if (Math.Abs(_queueDescriptionColumnWidth - value) < 0.5)
            {
                return;
            }

            _queueDescriptionColumnWidth = value;
            OnPropertyChanged();
        }
    }

    public MainWindow()
    {
        _settings = AppSettingsStore.Load();
        _api = new BridgeApiClient(_settings.BridgeBaseUrl);

        InitializeComponent();
        AppIconHelper.HookWindow(this);
        _hwnd = WindowNative.GetWindowHandle(this);
        _edgeDock = new EdgeDockController(this, DockRoot);
        SystemSnapDisabler.Attach(this);
        Closed += (_, _) =>
        {
            PersistWindowBounds();
            PersistColumnWidths();
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

        _windowBoundsTimer = _ui.CreateTimer();
        _windowBoundsTimer.Interval = TimeSpan.FromMilliseconds(400);
        _windowBoundsTimer.Tick += (_, _) =>
        {
            _windowBoundsTimer.Stop();
            PersistWindowBounds();
        };

        _columnSaveTimer = _ui.CreateTimer();
        _columnSaveTimer.Interval = TimeSpan.FromMilliseconds(400);
        _columnSaveTimer.Tick += (_, _) =>
        {
            _columnSaveTimer.Stop();
            PersistColumnWidths();
        };

        AppWindow.Changed += OnMainAppWindowChanged;

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

        var uiFontScale = _settings.UiFontScalePercent / 100.0;
        TxtStatusLine.FontSize = 11 * uiFontScale;
        TxtResultLine.FontSize = 11 * uiFontScale;
        BtnRefresh.FontSize = 12 * uiFontScale;
        BtnSettings.FontSize = 12 * uiFontScale;
        BtnSend.FontSize = 12 * uiFontScale;
        QueueListFontSize = _settings.QueueListFontSize;
        QueueList.MinHeight = Math.Max(72, _settings.QueueListFontSize * 2.5);

        if (initialize && !TryRestoreSavedWindowBounds())
        {
            ApplyDefaultWindowSize();
        }

        ApplyQueueColumnWidths();
        ApplyColumnHeaderVisibility();

        UpdateReconnectTimerState();

        if (!initialize && _timersStarted)
        {
            _ = RefreshAllAsync();
        }
    }

    private void OnMainAppWindowChanged(AppWindow sender, AppWindowChangedEventArgs args)
    {
        if (_suppressWindowSave || (!args.DidPositionChange && !args.DidSizeChange))
        {
            return;
        }

        _windowBoundsTimer.Stop();
        _windowBoundsTimer.Start();

        if (args.DidSizeChange && !_columnSplitterDragging && !_columnSplitterPointerDown)
        {
            ApplyQueueColumnWidths();
        }
    }

    private void OnQueueListSizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (_columnSplitterDragging || _columnSplitterPointerDown)
        {
            return;
        }

        ApplyQueueColumnWidths();
    }

    private void ApplyColumnHeaderVisibility()
    {
        QueueColumnHeader.Visibility = _settings.ShowQueueColumnHeader
            ? Visibility.Visible
            : Visibility.Collapsed;
    }

    private void OnHeaderColumnSplitterPointerPressed(object sender, PointerRoutedEventArgs e)
    {
        if (!_settings.ShowQueueColumnHeader)
        {
            return;
        }

        _columnSplitterPointerDown = true;
        _columnSplitterDragging = false;
        _columnDragPointerId = e.Pointer.PointerId;
        _columnDragPointer = e.Pointer;
        _columnDragStartX = e.GetCurrentPoint(QueueColumnHeader).Position.X;
        _columnDragStartNameW = QueueNameColumnWidth;
        HeaderColumnSplitter.CapturePointer(e.Pointer);
        HeaderColumnSplitter.Opacity = 1;
        e.Handled = true;
    }

    private void OnHeaderColumnSplitterPointerMoved(object sender, PointerRoutedEventArgs e)
    {
        if (!_columnSplitterPointerDown || e.Pointer.PointerId != _columnDragPointerId)
        {
            return;
        }

        var delta = e.GetCurrentPoint(QueueColumnHeader).Position.X - _columnDragStartX;
        if (!_columnSplitterDragging)
        {
            if (Math.Abs(delta) < ColumnDragThresholdDip)
            {
                return;
            }

            _columnSplitterDragging = true;
        }

        var available = GetQueueContentWidthDip();
        if (available <= 0)
        {
            return;
        }

        var maxName = available - QueueDescColumnMinDip - QueueColumnSplitterDip;
        var nameW = Math.Clamp(_columnDragStartNameW + delta, QueueNameColumnMinDip, maxName);
        var descW = available - nameW - QueueColumnSplitterDip;
        SetColumnWidths(nameW, descW, persist: false);
        e.Handled = true;
    }

    private void OnHeaderColumnSplitterPointerReleased(object sender, PointerRoutedEventArgs e)
    {
        if (!_columnSplitterPointerDown || e.Pointer.PointerId != _columnDragPointerId)
        {
            return;
        }

        EndHeaderColumnSplitterDrag(persist: _columnSplitterDragging);
        e.Handled = true;
    }

    private void OnHeaderColumnSplitterDoubleTapped(object sender, DoubleTappedRoutedEventArgs e)
    {
        EndHeaderColumnSplitterDrag(persist: false);
        _settings.QueueNameColumnWidth = 0;
        _settings.QueueDescriptionColumnWidth = 0;
        AutoExpandQueueColumnsFromText();
        SetColumnWidths(QueueNameColumnWidth, QueueDescriptionColumnWidth, persist: true);
        e.Handled = true;
    }

    private void EndHeaderColumnSplitterDrag(bool persist)
    {
        if (!_columnSplitterPointerDown && !_columnSplitterDragging)
        {
            return;
        }

        _columnSplitterPointerDown = false;
        _columnSplitterDragging = false;
        if (_columnDragPointer is not null)
        {
            HeaderColumnSplitter.ReleasePointerCapture(_columnDragPointer);
            _columnDragPointer = null;
        }
        HeaderColumnSplitter.Opacity = 0.7;
        if (persist)
        {
            SetColumnWidths(QueueNameColumnWidth, QueueDescriptionColumnWidth, persist: true);
        }
    }

    private void PersistWindowBounds()
    {
        var bounds = _edgeDock.GetRestorableBounds();
        if (bounds.Width <= 0 || bounds.Height <= 0)
        {
            return;
        }

        _settings.WindowLeft = bounds.X;
        _settings.WindowTop = bounds.Y;
        _settings.WindowWidth = bounds.Width;
        _settings.WindowHeight = bounds.Height;
        AppSettingsStore.Save(_settings);
    }

    private bool TryRestoreSavedWindowBounds()
    {
        var left = _settings.WindowLeft;
        var top = _settings.WindowTop;
        var width = _settings.WindowWidth;
        var height = _settings.WindowHeight;
        if (left is not int x || top is not int y || width is not int w || height is not int h
            || w < QueueWindowLimits.MinWidthDip || h < QueueWindowLimits.MinHeightDip)
        {
            return false;
        }

        var work = NativeWindowPositioner.GetWorkArea(_hwnd);
        if (work.Width <= 0 || work.Height <= 0)
        {
            return false;
        }

        w = Math.Min(Math.Max(w, QueueWindowLimits.MinWidthDip), work.Width);
        h = Math.Min(Math.Max(h, QueueWindowLimits.MinHeightDip), work.Height);
        x = Math.Clamp(x, work.X, work.X + work.Width - w);
        y = Math.Clamp(y, work.Y, work.Y + work.Height - h);

        _suppressWindowSave = true;
        try
        {
            AppWindow.MoveAndResize(new RectInt32(x, y, w, h));
            _edgeDock.NotifyShownBoundsChanged(new RectInt32(x, y, w, h));
        }
        finally
        {
            _suppressWindowSave = false;
        }

        return true;
    }

    private void ApplyDefaultWindowSize()
    {
        _suppressWindowSave = true;
        try
        {
            AppWindow.Resize(new SizeInt32(DefaultWidth, DefaultHeight));
            _edgeDock.NotifyShownBoundsChanged(NativeWindowPositioner.GetBounds(_hwnd));
        }
        finally
        {
            _suppressWindowSave = false;
        }
    }

    private void ApplyQueueColumnWidths()
    {
        if (_columnSplitterDragging || _columnSplitterPointerDown)
        {
            return;
        }

        if (HasSavedColumnWidths())
        {
            ApplyColumnWidths(_settings.QueueNameColumnWidth, _settings.QueueDescriptionColumnWidth);
            return;
        }

        AutoExpandQueueColumnsFromText();
    }

    private bool HasSavedColumnWidths() =>
        _settings.QueueNameColumnWidth > 0 && _settings.QueueDescriptionColumnWidth > 0;

    private void AutoExpandQueueColumnsFromText()
    {
        var fontSize = _settings.QueueListFontSize;
        var maxName = 0.0;
        var maxDesc = 0.0;
        foreach (var item in _items)
        {
            maxName = Math.Max(maxName, QueueTextMeasurer.MeasureWidth(item.SampleNameDisplay, fontSize));
            maxDesc = Math.Max(maxDesc, QueueTextMeasurer.MeasureWidth(item.SampleDescriptionDisplay, fontSize));
        }

        var nameW = Math.Max(QueueNameColumnMinDip, Math.Ceiling(maxName) + QueueColumnMeasurePadDip);
        var descW = Math.Max(QueueDescColumnMinDip, Math.Ceiling(maxDesc) + QueueColumnMeasurePadDip);

        var available = GetQueueContentWidthDip();
        if (available > 0 && nameW + descW + QueueColumnSplitterDip > available)
        {
            var overflow = nameW + descW + QueueColumnSplitterDip - available;
            descW = Math.Max(QueueDescColumnMinDip, descW - overflow);
            if (nameW + descW + QueueColumnSplitterDip > available)
            {
                nameW = Math.Max(QueueNameColumnMinDip, available - descW - QueueColumnSplitterDip);
            }
        }

        ApplyColumnWidths(nameW, descW);
    }

    private void ApplyColumnWidths(double nameWidth, double descWidth)
    {
        var available = GetQueueContentWidthDip();
        if (available > 0)
        {
            var maxName = Math.Max(QueueNameColumnMinDip, available - QueueDescColumnMinDip - QueueColumnSplitterDip);
            nameWidth = Math.Clamp(nameWidth, QueueNameColumnMinDip, maxName);
            descWidth = Math.Clamp(descWidth, QueueDescColumnMinDip, available - nameWidth - QueueColumnSplitterDip);
        }

        QueueNameColumnWidth = nameWidth;
        QueueDescriptionColumnWidth = descWidth;
        QueueNameColumnLength = new GridLength(nameWidth);
        QueueDescriptionColumnLength = new GridLength(descWidth);
        SyncQueueColumnGridWidths();
        _ui.TryEnqueue(SyncQueueColumnGridWidths);
    }

    private void SyncQueueColumnGridWidths()
    {
        if (QueueColumnHeader.ColumnDefinitions.Count >= 3)
        {
            QueueColumnHeader.ColumnDefinitions[0].Width = QueueNameColumnLength;
            QueueColumnHeader.ColumnDefinitions[2].Width = QueueDescriptionColumnLength;
        }

        for (var i = 0; i < QueueList.Items.Count; i++)
        {
            if (QueueList.ContainerFromIndex(i) is not ListViewItem { ContentTemplateRoot: Grid grid } ||
                grid.ColumnDefinitions.Count < 3)
            {
                continue;
            }

            grid.ColumnDefinitions[0].Width = QueueNameColumnLength;
            grid.ColumnDefinitions[2].Width = QueueDescriptionColumnLength;
        }

        QueueList.InvalidateMeasure();
    }

    private void SetColumnWidths(double nameWidth, double descWidth, bool persist)
    {
        ApplyColumnWidths(nameWidth, descWidth);
        if (!persist)
        {
            return;
        }

        _settings.QueueNameColumnWidth = QueueNameColumnWidth;
        _settings.QueueDescriptionColumnWidth = QueueDescriptionColumnWidth;
        _columnSaveTimer.Stop();
        _columnSaveTimer.Start();
    }

    private void PersistColumnWidths()
    {
        if (QueueNameColumnWidth <= 0 || QueueDescriptionColumnWidth <= 0)
        {
            return;
        }

        _settings.QueueNameColumnWidth = QueueNameColumnWidth;
        _settings.QueueDescriptionColumnWidth = QueueDescriptionColumnWidth;
        AppSettingsStore.Save(_settings);
    }

    private double GetQueueContentWidthDip()
    {
        var width = QueueColumnHeader.ActualWidth;
        if (width <= 0)
        {
            width = QueueList.ActualWidth;
        }

        if (width <= 0)
        {
            var scale = NativeWindowPositioner.GetScale(_hwnd);
            if (scale <= 0)
            {
                scale = 1.0;
            }

            var bounds = NativeWindowPositioner.GetBounds(_hwnd);
            width = bounds.Width / scale - 16;
        }

        return Math.Max(0, width);
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
            ShowSendResult("请先选中条目（单击多选，或双击单条发送）", isError: true);
            return;
        }

        await SendQueueByIdsAsync(ids);
    }

    private async void OnQueueListDoubleTapped(object sender, Microsoft.UI.Xaml.Input.DoubleTappedRoutedEventArgs e)
    {
        if (QueueList.SelectedItem is not QueueItemViewModel item || string.IsNullOrWhiteSpace(item.Id))
        {
            return;
        }

        await SendQueueByIdsAsync(new List<string> { item.Id });
    }

    private async Task SendQueueByIdsAsync(IReadOnlyList<string> ids)
    {
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

            var sendOk = data is { Ok: true };
            var resultLine = SendResultFormatter.FormatOneLine(data, _hasWebCredentials);
            if (sendOk && _settings.AutoClickInstrumentUi)
            {
                var click = await _uiAutomation.RunPostSendSequenceAsync(_settings);
                if (!click.Ok)
                {
                    resultLine += $" · UI 点击：{click.Message}";
                }
                else
                {
                    resultLine += " · UI 已自动确认";
                }
            }

            ShowSendResult(resultLine, isError: !sendOk);
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

        ApplyQueueColumnWidths();
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

        var online = data.BusinessOnline || data.InstrumentOnline || data.UpstreamConnected;
        var upstream = online ? "仪器在线" : "仪器离线";
        var rcs = string.IsNullOrWhiteSpace(data.RemoteControlState) ? "—" : data.RemoteControlState;
        var line =
            $"Bridge · {upstream} · 队列 {data.QueueCount}/{data.QueueMax} · RCS {rcs}";
        if (data.HeartbeatFailStreak > 0 || data.CommandFailStreak > 0)
        {
            line += $" · HB{data.HeartbeatFailStreak} CMD{data.CommandFailStreak}";
        }
        if (data.RecvBufferBytes > 0)
        {
            line += $" · buf{data.RecvBufferBytes}";
        }
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

    private void OnPropertyChanged([CallerMemberName] string? propertyName = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
}
