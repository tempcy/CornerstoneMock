using System.Runtime.InteropServices;
using System.Text;
using CornerstoneQueue.Models;
using FlaUI.Core.AutomationElements;
using FlaUI.Core.Conditions;
using FlaUI.Core.Definitions;
using FlaUI.UIA3;

namespace CornerstoneQueue.Services;

public sealed record InstrumentUiClickResult(bool Ok, string Message);

public sealed class InstrumentUiAutomationService
{
    private static readonly string[] DefaultAddSampleAutomationIds =
    [
        "AddSampleButton",
        "AddSamplesButton",
        "AddSamplesView",
        "RemoteAddSamplesButton",
        "btnAddSamples",
    ];

    private static readonly string[] AddSampleNameHints =
    [
        "添加试样",
        "添加样品",
        "Add Sample",
        "Add Samples",
        "Add sample",
    ];

    /// <summary>Inspect 中通知列表项的 ClassName 片段（无 AutomationId 时用于定位）。</summary>
    private const string RemoteSampleNotificationClassMarker = "RemoteSampleLoginNotification";

    public Task<InstrumentUiClickResult> RunPostSendSequenceAsync(AppSettings settings) =>
        StaTaskScheduler.Run(() => RunPostSendSequenceCore(settings));

    public Task<string> InspectInstrumentUiAsync(AppSettings settings, int maxDepth = 10, int maxNodes = 800) =>
        StaTaskScheduler.Run(() => InspectCore(settings, maxDepth, maxNodes));

    public Task<InstrumentUiClickResult> TestClickSequenceAsync(AppSettings settings) =>
        RunPostSendSequenceAsync(settings);

    private static InstrumentUiClickResult RunPostSendSequenceCore(AppSettings settings)
    {
        if (!settings.AutoClickInstrumentUi)
        {
            return new InstrumentUiClickResult(false, "未启用自动点击仪器 UI");
        }

        using var automation = new UIA3Automation();
        try
        {
            var window = FindInstrumentWindow(automation, settings.InstrumentWindowTitleContains);
            if (window is null)
            {
                return new InstrumentUiClickResult(
                    false,
                    $"未找到标题包含「{settings.InstrumentWindowTitleContains}」的 Cornerstone 窗口");
            }

            if (!TryClickByAutomationId(
                    window,
                    settings.NotificationButtonAutomationId,
                    "消息/通知按钮",
                    out var notifyError))
            {
                return new InstrumentUiClickResult(false, notifyError);
            }

            Thread.Sleep(settings.UiClickDelayAfterNotificationMs);

            if (!TryClickAddSampleButton(window, settings, out var addError))
            {
                return new InstrumentUiClickResult(false, addError);
            }

            Thread.Sleep(settings.UiClickDelayBetweenStepsMs);
            return new InstrumentUiClickResult(true, "已点击消息按钮与添加试样按钮");
        }
        catch (Exception ex)
        {
            return new InstrumentUiClickResult(false, $"UI Automation 异常：{ex.Message}");
        }
    }

    private static bool TryClickAddSampleButton(
        AutomationElement window,
        AppSettings settings,
        out string error)
    {
        var ids = new List<string>();
        if (!string.IsNullOrWhiteSpace(settings.AddSampleButtonAutomationId))
        {
            ids.Add(settings.AddSampleButtonAutomationId.Trim());
        }

        foreach (var id in DefaultAddSampleAutomationIds)
        {
            if (!ids.Contains(id, StringComparer.OrdinalIgnoreCase))
            {
                ids.Add(id);
            }
        }

        foreach (var id in ids)
        {
            if (TryClickByAutomationId(window, id, $"添加试样 ({id})", out error))
            {
                return true;
            }
        }

        if (TryClickAddSampleInNotificationPanel(window, out error))
        {
            return true;
        }

        foreach (var hint in AddSampleNameHints)
        {
            if (TryClickByName(window, hint, out error))
            {
                return true;
            }
        }

        error =
            "未找到添加试样按钮。请确认已展开通知面板，或在设置中填写 AutomationId。";
        return false;
    }

    /// <summary>
    /// 添加试样在通知列表项内：AutomationId 为空，ClassName=Button，祖先含 RemoteSampleLoginNotificationViewModel。
    /// </summary>
    private static bool TryClickAddSampleInNotificationPanel(AutomationElement window, out string error)
    {
        error = "";
        var candidates = new List<(AutomationElement Button, int Score)>();

        foreach (var btn in window.FindAllDescendants(cf => cf.ByControlType(ControlType.Button)))
        {
            if (!btn.IsEnabled || btn.Properties.IsOffscreen.ValueOrDefault)
            {
                continue;
            }

            if ((btn.ClassName ?? "") != "Button")
            {
                continue;
            }

            if (!HasAncestorClass(btn, RemoteSampleNotificationClassMarker))
            {
                continue;
            }

            candidates.Add((btn, ScoreAddSampleButton(btn)));
        }

        if (candidates.Count == 0)
        {
            error = "通知面板内未找到 Button（RemoteSampleLoginNotification）";
            return false;
        }

        var best = candidates
            .OrderByDescending(c => c.Score)
            .ThenBy(c =>
            {
                var r = c.Button.BoundingRectangle;
                return r.IsEmpty ? double.MaxValue : r.Y;
            })
            .First();

        if (best.Score < 1)
        {
            error = "通知面板内未找到可识别的添加试样按钮";
            return false;
        }

        return TryClickElement(best.Button, "通知面板-添加试样", out error);
    }

    private static int ScoreAddSampleButton(AutomationElement button)
    {
        var score = 0;
        foreach (var hint in AddSampleNameHints)
        {
            if (ButtonContainsText(button, hint))
            {
                score += 10;
            }
        }

        var rect = button.BoundingRectangle;
        if (!rect.IsEmpty)
        {
            var w = rect.Width;
            var h = rect.Height;
            if (w is >= 120 and <= 400 && h is >= 30 and <= 80)
            {
                score += 3;
            }
        }

        if ((button.ClassName ?? "") == "Button")
        {
            score += 1;
        }

        return score;
    }

    private static bool ButtonContainsText(AutomationElement button, string hint)
    {
        if ((button.Name ?? "").Contains(hint, StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }

        foreach (var text in button.FindAllDescendants(cf => cf.ByControlType(ControlType.Text)))
        {
            if ((text.Name ?? "").Contains(hint, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }

        return false;
    }

    private static bool HasAncestorClass(AutomationElement el, string classMarker)
    {
        var current = el.Parent;
        var depth = 0;
        while (current is not null && depth < 30)
        {
            if ((current.ClassName ?? "").Contains(classMarker, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }

            current = current.Parent;
            depth++;
        }

        return false;
    }

    private static Window? FindInstrumentWindow(UIA3Automation automation, string titleContains)
    {
        var needle = (titleContains ?? "").Trim();
        if (needle.Length == 0)
        {
            needle = "Cornerstone";
        }

        var desktop = automation.GetDesktop();
        Window? best = null;
        var bestArea = 0.0;
        foreach (var w in desktop.FindAllChildren(cf => cf.ByControlType(ControlType.Window)))
        {
            var title = w.Name ?? "";
            if (!title.Contains(needle, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            var rect = w.BoundingRectangle;
            var area = rect.IsEmpty ? 0 : rect.Width * rect.Height;
            if (best is null || area > bestArea)
            {
                best = w.AsWindow();
                bestArea = area;
            }
        }

        return best;
    }

    private static bool TryClickByAutomationId(
        AutomationElement root,
        string automationId,
        string label,
        out string error)
    {
        error = "";
        if (string.IsNullOrWhiteSpace(automationId))
        {
            error = $"{label}：AutomationId 为空";
            return false;
        }

        var el = root.FindFirstDescendant(cf => cf.ByAutomationId(automationId));
        if (el is null)
        {
            error = $"未找到 {label}（AutomationId=\"{automationId}\"）";
            return false;
        }

        return TryClickElement(el, label, out error);
    }

    private static bool TryClickByName(AutomationElement root, string name, out string error)
    {
        error = "";
        var el = root.FindFirstDescendant(cf => cf.ByName(name));
        if (el is not null)
        {
            return TryClickElement(el, $"名称「{name}」", out error);
        }

        foreach (var candidate in root.FindAllDescendants())
        {
            var n = candidate.Name ?? "";
            if (n.Contains(name, StringComparison.OrdinalIgnoreCase))
            {
                return TryClickElement(candidate, $"名称含「{name}」", out error);
            }
        }

        error = $"未找到名称「{name}」的控件";
        return false;
    }

    private static bool TryClickElement(AutomationElement el, string label, out string error)
    {
        error = "";
        try
        {
            el.Click();
            return true;
        }
        catch (Exception ex)
        {
            try
            {
                var rect = el.BoundingRectangle;
                if (!rect.IsEmpty && rect.Width > 0 && rect.Height > 0)
                {
                    ClickScreenPoint(
                        rect.X + rect.Width / 2,
                        rect.Y + rect.Height / 2);
                    return true;
                }
            }
            catch
            {
                // fall through
            }

            error = $"{label}：点击失败 — {ex.Message}";
            return false;
        }
    }

    private static void ClickScreenPoint(double x, double y)
    {
        var ix = (int)Math.Round(x);
        var iy = (int)Math.Round(y);
        SetCursorPos(ix, iy);
        Thread.Sleep(40);
        mouse_event(MouseEventLeftDown, 0, 0, 0, UIntPtr.Zero);
        mouse_event(MouseEventLeftUp, 0, 0, 0, UIntPtr.Zero);
    }

    private static string InspectCore(AppSettings settings, int maxDepth, int maxNodes)
    {
        var sb = new StringBuilder();
        using var automation = new UIA3Automation();
        var window = FindInstrumentWindow(automation, settings.InstrumentWindowTitleContains);
        if (window is null)
        {
            return $"未找到标题包含「{settings.InstrumentWindowTitleContains}」的仪器窗口。\n请确认 Cornerstone 已启动。";
        }

        try
        {
            sb.AppendLine($"窗口: \"{window.Name}\"");
            sb.AppendLine($"进程 Id: {window.Properties.ProcessId.ValueOrDefault}");
            sb.AppendLine($"ClassName: {window.ClassName}");
            sb.AppendLine();
            sb.AppendLine("--- 控件树 ---");
            sb.AppendLine();

            var count = 0;
            WalkElement(window, sb, 0, maxDepth, maxNodes, ref count);
            sb.AppendLine();
            sb.AppendLine($"共 {count} 个节点（深度≤{maxDepth}，上限 {maxNodes}）");
            sb.AppendLine();
            sb.AppendLine("消息按钮: AutomationId=NotificationButton");
            sb.AppendLine("添加试样: 展开通知后，在 RemoteSampleLoginNotificationViewModel 下找 ClassName=Button。");
        }
        catch (Exception ex)
        {
            sb.AppendLine($"Inspect 异常: {ex.Message}");
        }

        return sb.ToString();
    }

    private static void WalkElement(
        AutomationElement el,
        StringBuilder sb,
        int depth,
        int maxDepth,
        int maxNodes,
        ref int count)
    {
        if (count >= maxNodes || depth > maxDepth)
        {
            return;
        }

        try
        {
            var indent = new string(' ', depth * 2);
            var aid = el.AutomationId ?? "";
            var name = el.Name ?? "";
            var cls = el.ClassName ?? "";
            var ct = el.ControlType.ToString();
            var rect = el.BoundingRectangle;
            var enabled = el.IsEnabled;
            var off = el.Properties.IsOffscreen.ValueOrDefault;

            var interesting =
                !string.IsNullOrWhiteSpace(aid)
                || !string.IsNullOrWhiteSpace(name)
                || cls.Contains("Button", StringComparison.OrdinalIgnoreCase)
                || cls.Contains("View", StringComparison.OrdinalIgnoreCase);

            if (interesting || depth <= 2)
            {
                sb.Append(indent);
                sb.Append($"[{ct}] Aid=\"{aid}\" Name=\"{name}\" Class=\"{cls}\"");
                sb.Append($" Rect={FormatRect(rect)} Enabled={enabled} Offscreen={off}");
                sb.AppendLine();
                count++;
            }
        }
        catch
        {
            // skip
        }

        foreach (var child in el.FindAllChildren())
        {
            WalkElement(child, sb, depth + 1, maxDepth, maxNodes, ref count);
            if (count >= maxNodes)
            {
                break;
            }
        }
    }

    private static string FormatRect(System.Drawing.Rectangle rect) =>
        rect.IsEmpty ? "empty" : $"l:{rect.Left} t:{rect.Top} r:{rect.Right} b:{rect.Bottom}";

    private const uint MouseEventLeftDown = 0x0002;
    private const uint MouseEventLeftUp = 0x0004;

    [DllImport("user32.dll")]
    private static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    private static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
}
