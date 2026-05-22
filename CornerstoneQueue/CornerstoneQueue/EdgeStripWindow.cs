using CornerstoneQueue.Services;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;
using Windows.UI;
using WinRT.Interop;

namespace CornerstoneQueue;

/// <summary>
/// 左右贴边时的细条感应窗（主窗隐藏后显示）。受系统最小宽度限制时，用窗口 Region 只显示贴边蓝条。
/// </summary>
public sealed class EdgeStripWindow : Window
{
    public const int StripWidthDip = 12;

    private readonly IntPtr _hwnd;
    private readonly Border _bar;

    public event EventHandler? StripPointerEntered;

    public EdgeStripWindow()
    {
        _hwnd = WindowNative.GetWindowHandle(this);
        SystemBackdrop = null;

        var presenter = OverlappedPresenter.CreateForToolWindow();
        presenter.IsResizable = false;
        presenter.IsMaximizable = false;
        presenter.IsMinimizable = false;
        presenter.SetBorderAndTitleBar(false, false);
        AppWindow.SetPresenter(presenter);
        AppIconHelper.HookWindow(this);

        _bar = new Border
        {
            Width = StripWidthDip,
            BorderThickness = new Thickness(0),
            VerticalAlignment = VerticalAlignment.Stretch,
            Background = new SolidColorBrush(Color.FromArgb(210, 0, 120, 215)),
        };
        _bar.PointerEntered += (_, _) => StripPointerEntered?.Invoke(this, EventArgs.Empty);

        var root = new Grid
        {
            Background = new SolidColorBrush(Color.FromArgb(0, 0, 0, 0)),
        };
        root.Children.Add(_bar);
        Content = root;
        Closed += (_, _) => NativeWindowPositioner.ClearWindowRegion(_hwnd);
        NativeWindowPositioner.ApplyEdgeStripChrome(_hwnd);
    }

    public void Place(RectInt32 work, bool isLeft, int y, int height)
    {
        var minW = NativeWindowPositioner.GetMinTrackWidth(_hwnd);
        var barW = NativeWindowPositioner.ScaleToPhysical(_hwnd, StripWidthDip);
        var windowW = Math.Max(minW, barW);
        var h = Math.Max(
            NativeWindowPositioner.ScaleToPhysical(_hwnd, height),
            NativeWindowPositioner.ScaleToPhysical(_hwnd, 80));
        var yPx = Math.Clamp(y, work.Y, work.Y + work.Height - h);

        // 窗体大部分伸出屏外，仅贴边一侧 barW 留在工作区内；蓝条对齐该可见侧。
        int x;
        bool barOnLeftInWindow;
        if (isLeft)
        {
            x = work.X - (windowW - barW);
            _bar.HorizontalAlignment = HorizontalAlignment.Right;
            barOnLeftInWindow = false;
        }
        else
        {
            x = work.X + work.Width - barW;
            _bar.HorizontalAlignment = HorizontalAlignment.Left;
            barOnLeftInWindow = true;
        }

        NativeWindowPositioner.Move(_hwnd, new RectInt32(x, yPx, windowW, h));
        NativeWindowPositioner.ApplyEdgeStripChrome(_hwnd);
        NativeWindowPositioner.SetStripRegion(_hwnd, windowW, h, barW, barOnLeftInWindow);
    }

    public void SetTopmost(bool onTop)
    {
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.IsAlwaysOnTop = onTop;
        }
    }

}
