using CornerstoneQueue.Models;

namespace CornerstoneQueue.ViewModels;

public sealed class QueueItemViewModel : IEquatable<QueueItemViewModel>
{
    public QueueItemViewModel(QueueItemDto dto)
    {
        Id = dto.Id ?? "";
        SampleName = dto.SampleName ?? "";
        SampleDescription = dto.SampleDescription ?? "";
        ReceivedAtText = dto.ReceivedAtText ?? "";
        Peer = dto.Peer ?? "";
        Xml = dto.Xml ?? "";
        DisplayLine = BuildDisplayLine(SampleName, SampleDescription);
    }

    public string Id { get; }
    public string SampleName { get; }
    public string SampleDescription { get; }
    public string ReceivedAtText { get; }
    public string Peer { get; }
    public string Xml { get; }
    public string DisplayLine { get; }

    private static string BuildDisplayLine(string name, string description)
    {
        var n = string.IsNullOrWhiteSpace(name) ? "—" : name.Trim();
        var d = string.IsNullOrWhiteSpace(description) ? "—" : description.Trim();
        return $"{n} → {d}";
    }

    public static string Fingerprint(IEnumerable<QueueItemDto> items)
    {
        var parts = items
            .OrderBy(i => i.Id, StringComparer.Ordinal)
            .Select(i =>
                $"{i.Id}\t{i.SampleName}\t{i.SampleDescription}\t{i.ReceivedAt}\t{i.ReceivedAtText}\t{i.Peer}");
        return string.Join("\n", parts);
    }

    public bool Equals(QueueItemViewModel? other) =>
        other != null && Id == other.Id && DisplayLine == other.DisplayLine;

    public override bool Equals(object? obj) => obj is QueueItemViewModel other && Equals(other);

    public override int GetHashCode() => HashCode.Combine(Id, DisplayLine);
}
