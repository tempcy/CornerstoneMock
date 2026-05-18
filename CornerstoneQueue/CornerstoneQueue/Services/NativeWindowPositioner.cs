using System.Runtime.InteropServices;
using Windows.Graphics;

namespace CornerstoneQueue.Services;

internal static class NativeWindowPositioner
{
    private const uint SwpNoZOrder = 0x0004;
    private const uint SwpNoActivate = 0x0010;
    private const uint SwpNoMove = 0x0002;
    private const uint SwpNoSize = 0x0001;
    private const uint SwpFrameChanged = 0x0020;
    private const uint MonitorDefaultToNearest = 2;
    private const int GwlStyle = -16;
    private const int GwlExstyle = -20;
    private const int WmGetMinMaxInfo = 0x0024;

    private const int DwmwaNcrRenderingEnabled = 1;
    private const int DwmwaNcrRenderingPolicy = 2;
    private const int DwmwaTransitionsForcedisabled = 3;
    private const int DwmwaWindowCornerPreference = 33;
    private const int DwmwaBorderColor = 34;
    private const int DwmwaCaptionColor = 35;
    private const int DwmwaVisibleFrameBorderThickness = 37;
    private const int DwmncrpDisabled = 1;
    private const int DwmwcpDoNotRound = 2;
    private const int DwmwaColorNone = unchecked((int)0xFFFFFFFE);

    private const long WsBorder = 0x00800000L;
    private const long WsCaption = 0x00C00000L;
    private const long WsThickframe = 0x00040000L;
    private const long WsDlgframe = 0x00400000L;
    private const long WsSysmenu = 0x00080000L;
    private const long WsMaximizebox = 0x00010000L;
    private const long WsMinimizebox = 0x00020000L;
    private const long WsExWindowedge = 0x00000100L;
    private const long WsExClientedge = 0x00000200L;
    private const long WsExDlgmodalframe = 0x00000001L;
    private const long WsExStaticedge = 0x00020000L;
    private const long WsExToolwindow = 0x00000080L;

    public static void Move(IntPtr hwnd, RectInt32 rect)
    {
        if (hwnd == IntPtr.Zero)
        {
            return;
        }

        SetWindowPos(hwnd, IntPtr.Zero, rect.X, rect.Y, rect.Width, rect.Height, SwpNoZOrder | SwpNoActivate);
    }

    public static RectInt32 GetBounds(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero || !GetWindowRect(hwnd, out var rect))
        {
            return default;
        }

        return new RectInt32(rect.Left, rect.Top, rect.Right - rect.Left, rect.Bottom - rect.Top);
    }

    public static uint GetDpi(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero)
        {
            return 96;
        }

        var dpi = GetDpiForWindow(hwnd);
        return dpi == 0 ? 96u : dpi;
    }

    public static double GetScale(IntPtr hwnd) => GetDpi(hwnd) / 96.0;

    public static int ScaleToPhysical(IntPtr hwnd, int dip) =>
        (int)Math.Round(dip * GetScale(hwnd));

    public static RectInt32 GetWorkArea(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero)
        {
            return default;
        }

        var hMonitor = MonitorFromWindow(hwnd, MonitorDefaultToNearest);
        if (hMonitor == IntPtr.Zero)
        {
            return default;
        }

        var info = new MonitorInfo { cbSize = Marshal.SizeOf<MonitorInfo>() };
        if (!GetMonitorInfo(hMonitor, ref info))
        {
            return default;
        }

        var w = info.rcWork;
        return new RectInt32(w.Left, w.Top, w.Right - w.Left, w.Bottom - w.Top);
    }

    public static int GetMinTrackWidth(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero)
        {
            return ScaleToPhysical(hwnd, 140);
        }

        var mmi = new MinMaxInfo();
        var ptr = Marshal.AllocHGlobal(Marshal.SizeOf<MinMaxInfo>());
        try
        {
            Marshal.StructureToPtr(mmi, ptr, false);
            _ = SendMessage(hwnd, WmGetMinMaxInfo, IntPtr.Zero, ptr);
            mmi = Marshal.PtrToStructure<MinMaxInfo>(ptr)!;
            var minW = mmi.ptMinTrackSize.X;
            if (minW > 0)
            {
                return minW;
            }
        }
        finally
        {
            Marshal.FreeHGlobal(ptr);
        }

        return ScaleToPhysical(hwnd, 140);
    }

    public static void ClearWindowRegion(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero)
        {
            return;
        }

        SetWindowRgn(hwnd, IntPtr.Zero, true);
    }

    /// <summary>将可见/命中区域裁剪为窗内贴边一侧的细条（其余客户区在屏外）。</summary>
    public static void SetStripRegion(IntPtr hwnd, int windowWidth, int windowHeight, int barWidthPx, bool barOnLeftInWindow)
    {
        if (hwnd == IntPtr.Zero || windowWidth <= 0 || windowHeight <= 0 || barWidthPx <= 0)
        {
            return;
        }

        var barW = Math.Min(barWidthPx, windowWidth);
        var x0 = barOnLeftInWindow ? 0 : windowWidth - barW;
        var rgn = CreateRectRgn(x0, 0, x0 + barW, windowHeight);
        if (rgn == IntPtr.Zero)
        {
            return;
        }

        _ = SetWindowRgn(hwnd, rgn, true);
    }

    /// <summary>去掉细条窗非客户区边框、圆角与 DWM 阴影（WinUI 无边框窗仍可能带系统描边）。</summary>
    public static void ApplyEdgeStripChrome(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero)
        {
            return;
        }

        var style = GetWindowLongPtr(hwnd, GwlStyle).ToInt64();
        style &= ~(WsCaption | WsThickframe | WsBorder | WsDlgframe | WsSysmenu | WsMaximizebox | WsMinimizebox);
        SetWindowLongPtr(hwnd, GwlStyle, new IntPtr(style));

        var ex = GetWindowLongPtr(hwnd, GwlExstyle).ToInt64();
        ex |= WsExToolwindow;
        ex &= ~(WsExWindowedge | WsExClientedge | WsExDlgmodalframe | WsExStaticedge);
        SetWindowLongPtr(hwnd, GwlExstyle, new IntPtr(ex));

        _ = SetWindowTheme(hwnd, string.Empty, string.Empty);

        var disabled = 0;
        _ = DwmSetWindowAttribute(hwnd, DwmwaNcrRenderingEnabled, ref disabled, sizeof(int));

        var policy = DwmncrpDisabled;
        _ = DwmSetWindowAttribute(hwnd, DwmwaNcrRenderingPolicy, ref policy, sizeof(int));

        var noTransition = 1;
        _ = DwmSetWindowAttribute(hwnd, DwmwaTransitionsForcedisabled, ref noTransition, sizeof(int));

        var corner = DwmwcpDoNotRound;
        _ = DwmSetWindowAttribute(hwnd, DwmwaWindowCornerPreference, ref corner, sizeof(int));

        var colorNone = DwmwaColorNone;
        _ = DwmSetWindowAttribute(hwnd, DwmwaBorderColor, ref colorNone, sizeof(int));
        _ = DwmSetWindowAttribute(hwnd, DwmwaCaptionColor, ref colorNone, sizeof(int));

        var frameThickness = 0;
        _ = DwmSetWindowAttribute(hwnd, DwmwaVisibleFrameBorderThickness, ref frameThickness, sizeof(int));

        SetWindowPos(
            hwnd,
            IntPtr.Zero,
            0,
            0,
            0,
            0,
            SwpNoMove | SwpNoSize | SwpNoZOrder | SwpNoActivate | SwpFrameChanged);
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
    private struct PointNative
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MinMaxInfo
    {
        public PointNative ptReserved;
        public PointNative ptMaxSize;
        public PointNative ptMaxPosition;
        public PointNative ptMinTrackSize;
        public PointNative ptMaxTrackSize;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
    private struct MonitorInfo
    {
        public int cbSize;
        public RectNative rcMonitor;
        public RectNative rcWork;
        public uint dwFlags;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetWindowPos(
        IntPtr hWnd,
        IntPtr hWndInsertAfter,
        int x,
        int y,
        int cx,
        int cy,
        uint uFlags);

    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr hWnd, out RectNative lpRect);

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint dwFlags);

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    private static extern bool GetMonitorInfo(IntPtr hMonitor, ref MonitorInfo lpmi);

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    private static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern int SetWindowRgn(IntPtr hWnd, IntPtr hRgn, bool bRedraw);

    [DllImport("gdi32.dll")]
    private static extern IntPtr CreateRectRgn(int nLeftRect, int nTopRect, int nRightRect, int nBottomRect);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtr", SetLastError = true)]
    private static extern IntPtr GetWindowLongPtr(IntPtr hWnd, int nIndex);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtr", SetLastError = true)]
    private static extern IntPtr SetWindowLongPtr(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

    [DllImport("uxtheme.dll", CharSet = CharSet.Unicode)]
    private static extern int SetWindowTheme(IntPtr hwnd, string pszSubAppName, string pszSubIdList);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int attrValue, int attrSize);
}
