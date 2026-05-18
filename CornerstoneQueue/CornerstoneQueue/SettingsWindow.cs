using CornerstoneQueue.Models;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Graphics;

namespace CornerstoneQueue;

public sealed class SettingsWindow : Window
{
    private const int WindowWidth = 440;
    private const int WindowHeight = 560;

    private readonly SettingsPanel _panel;
    private readonly TextBlock _txtError;
    private readonly Action<AppSettings?> _onClosed;
    private AppSettings? _result;

    public SettingsWindow(AppSettings current, Action<AppSettings?> onClosed)
    {
        _onClosed = onClosed;
        Title = "Cornerstone 队列 — 设置";

        _panel = new SettingsPanel();
        _panel.LoadFrom(current);

        _txtError = new TextBlock
        {
            Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Colors.OrangeRed),
            TextWrapping = TextWrapping.Wrap,
            Visibility = Visibility.Collapsed,
        };

        var btnSave = new Button
        {
            Content = "保存",
            Style = (Style)Application.Current.Resources["AccentButtonStyle"],
            MinWidth = 96,
        };
        btnSave.Click += OnSaveClick;

        var btnCancel = new Button { Content = "取消", MinWidth = 96 };
        btnCancel.Click += OnCancelClick;

        var buttons = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            HorizontalAlignment = HorizontalAlignment.Right,
            Spacing = 8,
            Children = { btnCancel, btnSave },
        };

        var root = new Grid
        {
            Padding = new Thickness(20),
            RowDefinitions =
            {
                new RowDefinition { Height = new GridLength(1, GridUnitType.Star) },
                new RowDefinition { Height = GridLength.Auto },
                new RowDefinition { Height = GridLength.Auto },
                new RowDefinition { Height = GridLength.Auto },
            },
        };

        var scroll = new ScrollViewer
        {
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Content = _panel,
        };
        Grid.SetRow(scroll, 0);
        Grid.SetRow(_txtError, 1);
        Grid.SetRow(buttons, 3);

        root.Children.Add(scroll);
        root.Children.Add(_txtError);
        root.Children.Add(buttons);

        Content = root;

        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.IsResizable = true;
            presenter.IsAlwaysOnTop = false;
        }

        AppWindow.Resize(new SizeInt32(WindowWidth, WindowHeight));
        CenterOnScreen();

        Closed += (_, _) => _onClosed(_result);
    }

    private void CenterOnScreen()
    {
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary)?.WorkArea;
        if (area is null)
        {
            return;
        }

        var x = area.Value.X + (area.Value.Width - WindowWidth) / 2;
        var y = area.Value.Y + (area.Value.Height - WindowHeight) / 2;
        AppWindow.Move(new PointInt32(x, y));
    }

    private void OnSaveClick(object sender, RoutedEventArgs e)
    {
        var next = _panel.ToSettings();
        if (!TryValidateBridgeUrl(next.BridgeBaseUrl, out var error))
        {
            ShowError(error);
            return;
        }

        next.Normalize();
        _result = next;
        Close();
    }

    private void OnCancelClick(object sender, RoutedEventArgs e)
    {
        _result = null;
        Close();
    }

    private void ShowError(string message)
    {
        _txtError.Text = message;
        _txtError.Visibility = Visibility.Visible;
    }

    private static bool TryValidateBridgeUrl(string url, out string error)
    {
        error = "";
        if (!Uri.TryCreate(url.Trim(), UriKind.Absolute, out var uri))
        {
            error = "Bridge 地址格式无效，请使用 http:// 或 https://";
            return false;
        }

        if (uri.Scheme is not "http" and not "https")
        {
            error = "Bridge 地址须为 http 或 https";
            return false;
        }

        return true;
    }
}
