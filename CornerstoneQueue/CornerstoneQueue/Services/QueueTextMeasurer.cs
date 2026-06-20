using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace CornerstoneQueue.Services;

internal static class QueueTextMeasurer
{
    private const string MonoFontFamily = "Consolas";

    public static double MeasureWidth(string text, double fontSize)
    {
        if (string.IsNullOrEmpty(text))
        {
            return 0;
        }

        var block = new TextBlock
        {
            Text = text,
            FontSize = fontSize,
            FontFamily = new FontFamily(MonoFontFamily),
            TextWrapping = TextWrapping.NoWrap,
        };
        block.Measure(new Windows.Foundation.Size(double.PositiveInfinity, double.PositiveInfinity));
        return block.DesiredSize.Width;
    }
}
