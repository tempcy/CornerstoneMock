using System.Diagnostics;

namespace CornerstoneQueue;

/// <summary>
/// 单实例：启动前结束同名的其它 CornerstoneQueue 进程。
/// </summary>
internal static class SingleInstanceGuard
{
    private const string AppId = "cornerstone-queue";
    private const string ProcessName = "CornerstoneQueue";

    public static void EnsureRunning()
    {
        var current = Process.GetCurrentProcess();
        var killed = new List<int>();

        var lockPath = GetLockPath();
        if (File.Exists(lockPath))
        {
            if (int.TryParse(File.ReadAllText(lockPath).Trim(), out var oldPid)
                && oldPid > 0
                && oldPid != current.Id
                && TryKill(oldPid))
            {
                killed.Add(oldPid);
            }
        }

        foreach (var proc in Process.GetProcessesByName(ProcessName))
        {
            try
            {
                if (proc.Id == current.Id)
                {
                    continue;
                }

                if (TryKill(proc.Id))
                {
                    killed.Add(proc.Id);
                }
            }
            finally
            {
                proc.Dispose();
            }
        }

        if (killed.Count > 0)
        {
            Thread.Sleep(500);
        }

        Directory.CreateDirectory(Path.GetDirectoryName(lockPath)!);
        File.WriteAllText(lockPath, current.Id.ToString());
        AppDomain.CurrentDomain.ProcessExit += (_, _) => ReleaseLock(lockPath, current.Id);
    }

    private static string GetLockPath()
    {
        var baseDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
            "CornerstoneMock",
            "run");
        return Path.Combine(baseDir, $"{AppId}.pid");
    }

    private static bool TryKill(int pid)
    {
        try
        {
            using var p = Process.GetProcessById(pid);
            p.Kill(entireProcessTree: true);
            p.WaitForExit(3000);
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static void ReleaseLock(string lockPath, int ownerPid)
    {
        try
        {
            if (File.Exists(lockPath)
                && int.TryParse(File.ReadAllText(lockPath).Trim(), out var pid)
                && pid == ownerPid)
            {
                File.Delete(lockPath);
            }
        }
        catch
        {
            // ignore
        }
    }
}
