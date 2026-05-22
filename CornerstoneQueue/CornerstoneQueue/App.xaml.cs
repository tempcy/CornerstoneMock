using System.Text;
using Microsoft.UI.Xaml;

namespace CornerstoneQueue;

public partial class App : Application
{
    public App()
    {
        SingleInstanceGuard.EnsureRunning();

        try
        {
            InitializeComponent();
        }
        catch (Exception ex)
        {
            WriteCrashLog("App.InitializeComponent", ex);
            throw;
        }

        UnhandledException += OnUnhandledException;
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        try
        {
            var window = new MainWindow();
            window.Activate();
        }
        catch (Exception ex)
        {
            WriteCrashLog("OnLaunched", ex);
            throw;
        }
    }

    private void OnUnhandledException(object sender, Microsoft.UI.Xaml.UnhandledExceptionEventArgs e)
    {
        WriteCrashLog("UnhandledException", e.Exception);
        e.Handled = false;
    }

    private static void WriteCrashLog(string phase, Exception ex)
    {
        try
        {
            var dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "CornerstoneQueue");
            Directory.CreateDirectory(dir);
            var path = Path.Combine(dir, "startup-crash.log");
            var sb = new StringBuilder();
            sb.AppendLine(DateTimeOffset.Now.ToString("O"));
            sb.AppendLine($"phase={phase}");
            sb.AppendLine($"cwd={Environment.CurrentDirectory}");
            sb.AppendLine($"base={AppContext.BaseDirectory}");
            sb.AppendLine($"exe={Environment.ProcessPath}");
            sb.AppendLine(ex.ToString());
            File.AppendAllText(path, sb.ToString(), Encoding.UTF8);
        }
        catch
        {
            // ignore
        }
    }
}
