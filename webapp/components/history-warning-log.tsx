import type { PortfolioHistoryPayload } from '@/lib/types';

export function HistoryWarningLog({ history }: { history: PortfolioHistoryPayload }) {
  const snapshotsWithWarnings = history.snapshots.filter((snapshot) => snapshot.warning_count > 0).slice().reverse();

  return (
    <section className="surface-card history-warning-card">
      <div className="section-eyebrow">Historical Warnings</div>
      <h3 className="section-title">Past warning states are preserved</h3>
      <div className="section-subtle">Every stored snapshot keeps its warning list so you can audit stress periods later.</div>

      <div className="history-warning-log">
        {snapshotsWithWarnings.length === 0 ? (
          <div className="empty-state">No historical warnings stored yet.</div>
        ) : (
          snapshotsWithWarnings.map((snapshot) => (
            <article key={snapshot.recorded_at} className="history-warning-entry">
              <div className="history-warning-topline">
                <span>{new Date(snapshot.recorded_at).toLocaleString()}</span>
                <span className="soft-pill soft-pill-warning">{snapshot.warning_count} warning{snapshot.warning_count === 1 ? '' : 's'}</span>
              </div>
              <ul className="warning-list compact-warning-list">
                {snapshot.warnings.map((warning) => <li key={`${snapshot.recorded_at}-${warning}`}>{warning}</li>)}
              </ul>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
