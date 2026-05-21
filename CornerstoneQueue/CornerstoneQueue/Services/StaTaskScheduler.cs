using System.Collections.Concurrent;

namespace CornerstoneQueue.Services;

/// <summary>UI Automation 建议在 STA 线程调用。</summary>
internal sealed class StaTaskScheduler : TaskScheduler
{
    private static readonly Lazy<StaTaskScheduler> Lazy = new(() => new StaTaskScheduler());
    public static StaTaskScheduler Instance => Lazy.Value;

    private readonly BlockingCollection<Task> _queue = new();

    private StaTaskScheduler()
    {
        var thread = new Thread(ProcessQueue)
        {
            IsBackground = true,
            Name = "CornerstoneQueue-UIA-STA",
        };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
    }

    public static Task<T> Run<T>(Func<T> work, CancellationToken cancellationToken = default) =>
        Task.Factory.StartNew(work, cancellationToken, TaskCreationOptions.None, Instance);

    public static Task Run(Action work, CancellationToken cancellationToken = default) =>
        Task.Factory.StartNew(work, cancellationToken, TaskCreationOptions.None, Instance);

    protected override IEnumerable<Task>? GetScheduledTasks() => null;

    protected override void QueueTask(Task task) => _queue.Add(task);

    protected override bool TryExecuteTaskInline(Task task, bool _) => false;

    private void ProcessQueue()
    {
        foreach (var task in _queue.GetConsumingEnumerable())
        {
            TryExecuteTask(task);
        }
    }
}
