using System.Runtime.InteropServices;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Windows.Graphics;
using WinRT.Interop;

namespace CornerstoneQueue.Services;

/// <summary>
/// 滑出锁 + 隐藏锁。上侧：主窗滑出屏外；左右：主窗隐藏 + 独立细条窗。
/// </summary>
public sealed class EdgeDockController : IDisposable
{
    private enum DockEdge
    {
        None,
        Top,
        Left,
        Right,
    }

    private const int StripHitReachDip = 96;
    private const int DragSettleMs = 160;
    private const int HoverPollMs = 40;
    private const int HideOutsidePolls = 4;
    private const int MinExpandedWidthDip = 360;
    private const int MinExpandedHeightDip = 200;
    private const int TopStripDip = 12;

    private readonly Window _mainWindow;
    private readonly IntPtr _hwnd;
    private readonly AppWindow _appWindow;
    private readonly OverlappedPresenter? _presenter;
    private readonly DispatcherQueue _dispatcher;
    private readonly DispatcherQueueTimer _dragSettleTimer;
    private readonly DispatcherQueueTimer _hoverTimer;

    private EdgeStripWindow? _strip;
    private bool _slideOutLocked;
    private bool _hideLocked;
    private bool _programmaticMove;
    private bool _wasMinimized;
    private int _outsidePollStreak;
    private long _expandedAtMs;
    private DockEdge _edge = DockEdge.None;
    private RectInt32 _shownBounds;

    public EdgeDockController(Window mainWindow, FrameworkElement _)
    {
        _mainWindow = mainWindow;
        _hwnd = WindowNative.GetWindowHandle(mainWindow);
        _appWindow = mainWindow.AppWindow;
        _presenter = _appWindow.Presenter as OverlappedPresenter;
        _dispatcher = DispatcherQueue.GetForCurrentThread();

        _shownBounds = GetWindowBounds();

        _dragSettleTimer = _dispatcher.CreateTimer();
        _dragSettleTimer.Interval = TimeSpan.FromMilliseconds(DragSettleMs);
        _dragSettleTimer.Tick += (_, _) => OnDragSettled();

        _hoverTimer = _dispatcher.CreateTimer();
        _hoverTimer.Interval = TimeSpan.FromMilliseconds(HoverPollMs);
        _hoverTimer.IsRepeating = true;
        _hoverTimer.Tick += (_, _) => OnHoverPoll();

        _appWindow.Changed += OnAppWindowChanged;
    }

    public void SyncAlwaysOnTop(bool onTop)
    {
        _strip?.SetTopmost(onTop);
    }

    public void Dispose()
    {
        _strip?.Close();
        _strip = null;
        _appWindow.Changed -= OnAppWindowChanged;
        _dragSettleTimer.Stop();
        _hoverTimer.Stop();
    }

    private void OnAppWindowChanged(AppWindow sender, AppWindowChangedEventArgs args)
    {
        if (_programmaticMove || _hideLocked)
        {
            return;
        }

        if (args.DidVisibilityChange || args.DidPresenterChange)
        {
            HandlePresenterStateChange();
        }

        if (args.DidPositionChange || args.DidSizeChange)
        {
            if (!IsMinimized())
            {
                var bounds = GetWindowBounds();
                if (!_slideOutLocked || !_hideLocked)
                {
                    _shownBounds = bounds;
                }
            }

            _dragSettleTimer.Stop();
            _dragSettleTimer.Start();
        }
    }

    private void HandlePresenterStateChange()
    {
        if (IsMinimized())
        {
            _wasMinimized = true;
            _hoverTimer.Stop();
            return;
        }

        if (!_wasMinimized)
        {
            return;
        }

        _wasMinimized = false;
        UnlockSlideOut(restoreShown: true);
    }

    private bool IsMinimized() => _presenter?.State == OverlappedPresenterState.Minimized;

    private void OnDragSettled()
    {
        _dragSettleTimer.Stop();
        if (IsMinimized())
        {
            return;
        }

        // 贴边/解锁必须在标题栏拖放结束（松开左键）之后，否则会打断系统拖放并弹回起点
        if (IsLeftButtonDown())
        {
            _dragSettleTimer.Start();
            return;
        }

        if (_hideLocked && _edge is DockEdge.Left or DockEdge.Right)
        {
            return;
        }

        var bounds = GetWindowBounds();
        var work = GetWorkArea();

        if (!_slideOutLocked)
        {
            if (TryDetectDockEdge(bounds, work, out var edge))
            {
                EngageSlideOut(edge, work);
            }

            return;
        }

        if (!_hideLocked && !ShouldStayDocked(bounds, work, _edge))
        {
            UnlockSlideOut(restoreShown: true);
        }
        else if (!_hideLocked)
        {
            _shownBounds = ClampToWork(bounds, work);
        }
    }

    private void EngageSlideOut(DockEdge edge, RectInt32 work)
    {
        _slideOutLocked = true;
        _hideLocked = true;
        _edge = edge;
        _outsidePollStreak = 0;
        _shownBounds = ClampToWork(GetWindowBounds(), work);

        ApplyHiddenState(work);
        _hoverTimer.Start();
    }

    private void UnlockSlideOut(bool restoreShown)
    {
        _slideOutLocked = false;
        _hideLocked = false;
        _edge = DockEdge.None;
        _outsidePollStreak = 0;
        _hoverTimer.Stop();

        _strip?.AppWindow.Hide();
        _appWindow.Show();

        if (restoreShown)
        {
            MoveWindow(ClampToWork(_shownBounds, GetWorkArea()));
            _shownBounds = GetWindowBounds();
            _mainWindow.Activate();
        }
    }

    private void OnHoverPoll()
    {
        if (!_slideOutLocked || IsMinimized())
        {
            return;
        }

        if (!TryGetCursorPos(out var cursor))
        {
            return;
        }

        var work = GetWorkArea();
        var stripZone = GetStripHitZone(work);

        if (_hideLocked)
        {
            if (!IsLeftButtonDown() && stripZone.Contains(cursor))
            {
                ShowMainFromStrip();
            }

            return;
        }

        if (IsLeftButtonDown() || IsWithinExpandGrace())
        {
            _outsidePollStreak = 0;
            return;
        }

        var hit = InflateRect(ClampToWork(_shownBounds, work), 8);
        if (!hit.Contains(cursor) && !stripZone.Contains(cursor))
        {
            _outsidePollStreak++;
            if (_outsidePollStreak >= HideOutsidePolls)
            {
                _outsidePollStreak = 0;
                ApplyHiddenState(work);
            }
        }
        else
        {
            _outsidePollStreak = 0;
        }
    }

    private void ShowMainFromStrip()
    {
        if (!_slideOutLocked || !_hideLocked)
        {
            return;
        }

        _hideLocked = false;
        _outsidePollStreak = 0;
        _expandedAtMs = Environment.TickCount64;
        _strip?.AppWindow.Hide();
        _appWindow.Show();
        MoveWindow(ClampToWork(_shownBounds, GetWorkArea()));
        _mainWindow.Activate();
    }

    private void ApplyHiddenState(RectInt32 work)
    {
        _hideLocked = true;
        _outsidePollStreak = 0;

        if (_edge is DockEdge.Left or DockEdge.Right)
        {
            ApplyHiddenHorizontal(work);
            return;
        }

        ApplyHiddenTop(work);
    }

    private void ApplyHiddenHorizontal(RectInt32 work)
    {
        EnsureStrip();
        var h = Math.Min(_shownBounds.Height, work.Height);
        var y = Clamp(_shownBounds.Y, work.Y, work.Y + work.Height - h);
        _strip!.Place(work, _edge == DockEdge.Left, y, h);
        _strip.SetTopmost(_presenter?.IsAlwaysOnTop == true);
        _strip.Activate();
        _appWindow.Hide();
    }

    private void ApplyHiddenTop(RectInt32 work)
    {
        _strip?.AppWindow.Hide();
        _appWindow.Show();

        var full = ClampToWork(_shownBounds, work);
        var topStrip = NativeWindowPositioner.ScaleToPhysical(_hwnd, TopStripDip);
        var placement = new RectInt32(
            full.X,
            work.Y - full.Height + topStrip,
            full.Width,
            full.Height);
        MoveWindow(placement);
    }

    private void EnsureStrip()
    {
        if (_strip != null)
        {
            return;
        }

        _strip = new EdgeStripWindow();
        _strip.StripPointerEntered += (_, _) => ShowMainFromStrip();
    }

    private bool TryDetectDockEdge(RectInt32 bounds, RectInt32 work, out DockEdge edge)
    {
        edge = DockEdge.None;
        var cx = bounds.X + bounds.Width / 2.0;
        var cy = bounds.Y + bounds.Height / 2.0;
        var marginX = work.Width * 0.12;
        var marginY = work.Height * 0.10;

        var list = new List<(DockEdge E, double D)>();
        if (cx < work.X + marginX)
        {
            list.Add((DockEdge.Left, cx - work.X));
        }

        if (cx > work.X + work.Width - marginX)
        {
            list.Add((DockEdge.Right, work.X + work.Width - cx));
        }

        if (cy < work.Y + marginY)
        {
            list.Add((DockEdge.Top, cy - work.Y));
        }

        if (list.Count == 0)
        {
            return false;
        }

        edge = list.OrderBy(t => t.D).First().E;
        return true;
    }

    /// <summary>展开后是否仍贴在对应边缘（用窗体边沿判断，便于拖离解锁）。</summary>
    private static bool ShouldStayDocked(RectInt32 bounds, RectInt32 work, DockEdge edge)
    {
        var marginX = work.Width * 0.12;
        var marginY = work.Height * 0.10;
        return edge switch
        {
            DockEdge.Top => bounds.Y < work.Y + marginY + 32,
            DockEdge.Left => bounds.X < work.X + marginX,
            DockEdge.Right => bounds.X + bounds.Width > work.X + work.Width - marginX,
            _ => false,
        };
    }

    private bool IsWithinExpandGrace() =>
        _expandedAtMs > 0 && Environment.TickCount64 - _expandedAtMs < 900;

    private RectInt32 GetStripHitZone(RectInt32 work)
    {
        var reach = NativeWindowPositioner.ScaleToPhysical(_hwnd, StripHitReachDip);
        return _edge switch
        {
            DockEdge.Top => new RectInt32(work.X, work.Y, work.Width, reach),
            DockEdge.Left => new RectInt32(work.X, work.Y, reach, work.Height),
            DockEdge.Right => new RectInt32(work.X + work.Width - reach, work.Y, reach, work.Height),
            _ => default,
        };
    }

    private void MoveWindow(RectInt32 rect)
    {
        _programmaticMove = true;
        SystemSnapDisabler.EnterProgrammaticMove();
        try
        {
            _appWindow.MoveAndResize(rect);
        }
        finally
        {
            SystemSnapDisabler.ExitProgrammaticMove();
            _programmaticMove = false;
        }
    }

    private RectInt32 GetWindowBounds() => NativeWindowPositioner.GetBounds(_hwnd);

    private RectInt32 GetWorkArea() => NativeWindowPositioner.GetWorkArea(_hwnd);

    private RectInt32 ClampToWork(RectInt32 bounds, RectInt32 work)
    {
        var minW = NativeWindowPositioner.ScaleToPhysical(_hwnd, MinExpandedWidthDip);
        var minH = NativeWindowPositioner.ScaleToPhysical(_hwnd, MinExpandedHeightDip);
        var w = Math.Min(Math.Max(bounds.Width, minW), work.Width);
        var h = Math.Min(Math.Max(bounds.Height, minH), work.Height);
        return new RectInt32(
            Clamp(bounds.X, work.X, work.X + work.Width - w),
            Clamp(bounds.Y, work.Y, work.Y + work.Height - h),
            w,
            h);
    }

    private static int Clamp(int v, int min, int max) => v < min ? min : v > max ? max : v;

    private static RectInt32 InflateRect(RectInt32 r, int amount)
    {
        return new RectInt32(r.X - amount, r.Y - amount, r.Width + amount * 2, r.Height + amount * 2);
    }

    private static bool TryGetCursorPos(out PointInt32 p)
    {
        p = default;
        if (!GetCursorPos(out var pt))
        {
            return false;
        }

        p = new PointInt32(pt.X, pt.Y);
        return true;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PointNative
    {
        public int X;
        public int Y;
    }

    [DllImport("user32.dll")]
    private static extern bool GetCursorPos(out PointNative lpPoint);

    [DllImport("user32.dll")]
    private static extern short GetAsyncKeyState(int vKey);

    private static bool IsLeftButtonDown() => (GetAsyncKeyState(0x01) & 0x8000) != 0;
}

internal static class RectInt32Extensions
{
    public static bool Contains(this RectInt32 rect, PointInt32 point)
    {
        if (rect.Width <= 0 || rect.Height <= 0)
        {
            return false;
        }

        return point.X >= rect.X
               && point.X < rect.X + rect.Width
               && point.Y >= rect.Y
               && point.Y < rect.Y + rect.Height;
    }
}
