using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using WinRT.Interop;

namespace CornerstoneQueue.Services;

/// <summary>
/// 禁用本窗口触发的 Windows 贴边半边/最大化吸附，避免与自定义贴边隐藏冲突。
/// </summary>
public static class SystemSnapDisabler
{
    private const int GwlStyle = -16;
    private const int GwlpWndProc = -4;
    private const uint WsMaximizeBox = 0x00010000;

    private const uint WmWindowPosChanging = 0x0046;
    private const uint WmNcLButtonDblClk = 0x00A3;
    private const uint WmSysCommand = 0x0112;
    private const int ScMaximize = 0xF030;

    private const uint SwpNomove = 0x0002;
    private const uint SwpNosize = 0x0001;

    private static IntPtr _hwnd;
    private static IntPtr _oldWndProc;
    private static WndProcDelegate? _subclassProc;
    private static int _programmaticDepth;

    public static void Attach(Window window)
    {
        var hwnd = WindowNative.GetWindowHandle(window);
        if (hwnd == _hwnd)
        {
            return;
        }

        if (_hwnd != IntPtr.Zero)
        {
            Detach();
        }

        _hwnd = hwnd;

        if (window.AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.IsMaximizable = false;
        }

        var style = GetWindowLongPtr(hwnd, GwlStyle);
        SetWindowLongPtr(hwnd, GwlStyle, (IntPtr)((nint)style & ~(nint)WsMaximizeBox));

        _subclassProc = SubclassWndProc;
        _oldWndProc = SetWindowLongPtr(hwnd, GwlpWndProc, Marshal.GetFunctionPointerForDelegate(_subclassProc));
    }

    public static void Detach()
    {
        if (_hwnd == IntPtr.Zero || _oldWndProc == IntPtr.Zero)
        {
            return;
        }

        SetWindowLongPtr(_hwnd, GwlpWndProc, _oldWndProc);
        _hwnd = IntPtr.Zero;
        _oldWndProc = IntPtr.Zero;
        _subclassProc = null;
    }

    public static void EnterProgrammaticMove()
    {
        Interlocked.Increment(ref _programmaticDepth);
    }

    public static void ExitProgrammaticMove()
    {
        Interlocked.Decrement(ref _programmaticDepth);
    }

    private static bool IsProgrammaticMove => Volatile.Read(ref _programmaticDepth) > 0;

    private static IntPtr SubclassWndProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam)
    {
        switch (msg)
        {
            case WmNcLButtonDblClk:
                return IntPtr.Zero;

            case WmSysCommand:
                if ((wParam.ToInt32() & 0xFFF0) == ScMaximize)
                {
                    return IntPtr.Zero;
                }

                break;

            case WmWindowPosChanging:
                if (!IsProgrammaticMove && TryBlockSystemSnap(lParam))
                {
                    return IntPtr.Zero;
                }

                break;
        }

        return CallWindowProc(_oldWndProc, hWnd, msg, wParam, lParam);
    }

    private static bool TryBlockSystemSnap(IntPtr lParam)
    {
        var pos = Marshal.PtrToStructure<WindowPos>(lParam);
        if ((pos.flags & SwpNosize) != 0 && (pos.flags & SwpNomove) != 0)
        {
            return false;
        }

        if (!TryGetMonitorWorkArea(pos.x + Math.Max(pos.cx, 1) / 2, pos.y + Math.Max(pos.cy, 1) / 2, out var work))
        {
            return false;
        }

        if (!IsLikelySystemSnap(pos.x, pos.y, pos.cx, pos.cy, work))
        {
            return false;
        }

        pos.flags |= SwpNomove | SwpNosize;
        Marshal.StructureToPtr(pos, lParam, false);
        return true;
    }

    private static bool IsLikelySystemSnap(int x, int y, int cx, int cy, RectNative work)
    {
        const int tolerance = 12;
        var workW = work.Right - work.Left;
        var workH = work.Bottom - work.Top;
        if (workW <= 0 || workH <= 0)
        {
            return false;
        }

        if (Math.Abs(cx - workW) <= tolerance
            && Math.Abs(cy - workH) <= tolerance
            && Math.Abs(x - work.Left) <= tolerance
            && Math.Abs(y - work.Top) <= tolerance)
        {
            return true;
        }

        var halfW = workW / 2;
        if (Math.Abs(cx - halfW) <= tolerance
            && Math.Abs(x - work.Left) <= tolerance
            && Math.Abs(cy - workH) <= tolerance)
        {
            return true;
        }

        if (Math.Abs(cx - halfW) <= tolerance
            && Math.Abs((x + cx) - work.Right) <= tolerance
            && Math.Abs(cy - workH) <= tolerance)
        {
            return true;
        }

        return false;
    }

    private static bool TryGetMonitorWorkArea(int x, int y, out RectNative work)
    {
        work = default;
        var point = new PointNative { X = x, Y = y };
        var hMonitor = MonitorFromPoint(point, MonitorDefaultToNearest);
        if (hMonitor == IntPtr.Zero)
        {
            return false;
        }

        var info = new MonitorInfo { cbSize = Marshal.SizeOf<MonitorInfo>() };
        if (!GetMonitorInfo(hMonitor, ref info))
        {
            return false;
        }

        work = info.rcWork;
        return true;
    }

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    private delegate IntPtr WndProcDelegate(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    private struct PointNative
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct RectNative
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct WindowPos
    {
        public IntPtr hwnd;
        public IntPtr hwndInsertAfter;
        public int x;
        public int y;
        public int cx;
        public int cy;
        public uint flags;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
    private struct MonitorInfo
    {
        public int cbSize;
        public RectNative rcMonitor;
        public RectNative rcWork;
        public uint dwFlags;
    }

    private const uint MonitorDefaultToNearest = 2;

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtr")]
    private static extern IntPtr GetWindowLongPtr(IntPtr hWnd, int nIndex);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtr")]
    private static extern IntPtr SetWindowLongPtr(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

    [DllImport("user32.dll")]
    private static extern IntPtr CallWindowProc(IntPtr lpPrevWndFunc, IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern IntPtr MonitorFromPoint(PointNative pt, uint dwFlags);

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    private static extern bool GetMonitorInfo(IntPtr hMonitor, ref MonitorInfo lpmi);
}
