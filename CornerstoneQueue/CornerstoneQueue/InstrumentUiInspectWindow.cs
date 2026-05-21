using CornerstoneQueue.Services;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Graphics;

namespace CornerstoneQueue;

public sealed class InstrumentUiInspectWindow : Window
{
    public InstrumentUiInspectWindow(string report)
    {
        Title = "Inspect — Cornerstone 控件树";

        var box = new TextBox
        {
            Text = report,
            IsReadOnly = true,
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas"),
            FontSize = 12,
        };

        var btnClose = new Button { Content = "关闭", MinWidth = 96, HorizontalAlignment = HorizontalAlignment.Right };
        btnClose.Click += (_, _) => Close();

        var root = new Grid
        {
            Padding = new Thickness(16),
            RowDefinitions =
            {
                new RowDefinition { Height = new GridLength(1, GridUnitType.Star) },
                new RowDefinition { Height = GridLength.Auto },
            },
        };
        Grid.SetRow(box, 0);
        Grid.SetRow(btnClose, 1);
        root.Children.Add(box);
        root.Children.Add(btnClose);

        Content = root;
        AppIconHelper.HookWindow(this);

        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.IsResizable = true;
            presenter.IsAlwaysOnTop = false;
        }

        AppWindow.Resize(new SizeInt32(720, 520));
        CenterOnScreen();
    }

    private void CenterOnScreen()
    {
        const int w = 720;
        const int h = 520;
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary)?.WorkArea;
        if (area is null)
        {
            return;
        }

        AppWindow.Move(new PointInt32(
            area.Value.X + (area.Value.Width - w) / 2,
            area.Value.Y + (area.Value.Height - h) / 2));
    }
}
