using System.Text;
using System.Xml.Linq;
using CornerstoneQueue.Models;

namespace CornerstoneQueue.Services;

public static class SendResultFormatter
{
    /// <summary>底部单行结果摘要。</summary>
    public static string FormatOneLine(SendQueueResponse? response, bool hasWebCredentials)
    {
        if (response == null)
        {
            return "发送失败：无应答";
        }

        if (!response.Ok)
        {
            var err = response.Error ?? "发送失败";
            if (!hasWebCredentials)
            {
                err += "（未配置 web 账号）";
            }

            return err;
        }

        if (response.Results == null || response.Results.Count == 0)
        {
            return "发送完成";
        }

        var parts = new List<string> { "发送完成" };
        foreach (var item in response.Results)
        {
            var id = item.Id ?? "";
            var idShort = id.Length > 10 ? id[..10] + "…" : id;
            parts.Add($"{idShort}: {SummarizeUpstreamXml(item.UpstreamResponse)}");
        }

        return string.Join(" · ", parts);
    }

    public static string Format(SendQueueResponse? response, bool hasWebCredentials)
    {
        if (response == null)
        {
            return "发送失败：无应答。";
        }

        var sb = new StringBuilder();
        if (!response.Ok)
        {
            sb.AppendLine(response.Error ?? "发送失败");
            if (!hasWebCredentials)
            {
                sb.AppendLine();
                sb.Append("提示：Bridge 未配置 web_user / web_password，无法在网页通道登录仪器。");
            }
        }
        else
        {
            sb.AppendLine("发送完成（队列已保留 queueKept=true）");
        }

        if (response.Results == null || response.Results.Count == 0)
        {
            return sb.ToString().TrimEnd();
        }

        foreach (var item in response.Results)
        {
            sb.AppendLine();
            sb.AppendLine($"── {item.Id} ──");
            sb.Append(SummarizeUpstreamXml(item.UpstreamResponse));
        }

        return sb.ToString().TrimEnd();
    }

    private static string SummarizeUpstreamXml(string? xml)
    {
        if (string.IsNullOrWhiteSpace(xml))
        {
            return "（无上游应答）";
        }

        var trimmed = xml.Trim();
        if (trimmed.StartsWith("<Error", StringComparison.OrdinalIgnoreCase))
        {
            return trimmed.Length > 600 ? trimmed[..600] + "…" : trimmed;
        }

        try
        {
            var doc = XDocument.Parse(trimmed);
            var root = doc.Root;
            if (root == null)
            {
                return trimmed.Length > 400 ? trimmed[..400] + "…" : trimmed;
            }

            var ec = root.Attribute("ErrorCode")?.Value;
            var em = root.Attribute("ErrorMessage")?.Value;
            if (!string.IsNullOrEmpty(ec) && ec != "0")
            {
                return $"{root.Name.LocalName} ErrorCode={ec} {em}".Trim();
            }

            return $"{root.Name.LocalName} OK (ErrorCode=0)";
        }
        catch
        {
            return trimmed.Length > 500 ? trimmed[..500] + "…" : trimmed;
        }
    }
}
