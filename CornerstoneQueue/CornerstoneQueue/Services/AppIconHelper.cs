using System.Runtime.InteropServices;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using WinRT.Interop;

namespace CornerstoneQueue.Services;

/// <summary>
/// 为 WinUI 非打包应用设置 exe 与窗口/任务栏图标。
/// </summary>
public static class AppIconHelper
{
    private const int WmSetIcon = 0x0080;
    private const int IconSmall = 0;
    private const int IconBig = 1;
    private const uint ImageIcon = 1;
    private const uint LrLoadFromFile = 0x00000010;

    private static string? _resolvedPath;

    public static string? IconPath => _resolvedPath ??= ResolveIconPath();

    public static void ApplyToWindow(Window window)
    {
        var path = IconPath;
        if (string.IsNullOrEmpty(path))
        {
            return;
        }

        var fullPath = Path.GetFullPath(path);

        try
        {
            var hwnd = WindowNative.GetWindowHandle(window);
            var windowId = Win32Interop.GetWindowIdFromWindow(hwnd);
            var appWindow = AppWindow.GetFromWindowId(windowId);
            appWindow.SetIcon(fullPath);
            ApplyWin32WindowIcons(hwnd, fullPath);
        }
        catch
        {
            // 图标缺失不应影响主流程
        }
    }

    public static void HookWindow(Window window)
    {
        ApplyToWindow(window);
        window.Activated += OnWindowActivated;
    }

    private static void OnWindowActivated(object sender, WindowActivatedEventArgs e)
    {
        if (sender is Window w && e.WindowActivationState != WindowActivationState.Deactivated)
        {
            ApplyToWindow(w);
        }
    }

    private static string? ResolveIconPath()
    {
        var relative = Path.Combine("Assets", "CornerstoneQueue.ico");
        var bases = new[]
        {
            AppContext.BaseDirectory,
            Path.GetDirectoryName(Environment.ProcessPath) ?? "",
            Path.GetDirectoryName(typeof(AppIconHelper).Assembly.Location) ?? "",
        };

        foreach (var root in bases.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (string.IsNullOrWhiteSpace(root))
            {
                continue;
            }

            var candidate = Path.Combine(root, relative);
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }

        return null;
    }

    private static void ApplyWin32WindowIcons(IntPtr hwnd, string fullPath)
    {
        var big = LoadImage(IntPtr.Zero, fullPath, ImageIcon, 32, 32, LrLoadFromFile);
        var small = LoadImage(IntPtr.Zero, fullPath, ImageIcon, 16, 16, LrLoadFromFile);
        if (big != IntPtr.Zero)
        {
            SendMessage(hwnd, WmSetIcon, (IntPtr)IconBig, big);
        }

        if (small != IntPtr.Zero)
        {
            SendMessage(hwnd, WmSetIcon, (IntPtr)IconSmall, small);
        }
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr LoadImage(
        IntPtr hInst,
        string name,
        uint type,
        int cx,
        int cy,
        uint fuLoad);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);
}
