export function MethodologyPanel() {
  return (
    <div className="methodology">
      <p>
        Signals for day <code>t</code> use only information available through
        the close of <code>t−1</code>. A signal change executes at day{" "}
        <code>t</code>’s open.
      </p>
      <p>
        Portfolio equity is cash plus shares multiplied by the daily close.
        Shares are whole units, and transaction costs are charged as traded
        notional multiplied by the configured basis-point rate.
      </p>
      <p>
        The benchmark is buy-and-hold over the same requested period, purchased
        at the first selected open with the same transaction-cost assumption.
        This result is hypothetical research evidence, not a trading
        recommendation.
      </p>
      <p className="methodology-note">
        See the repository’s <code>METHODOLOGY.md</code> for the complete Phase
        1 assumptions and known sources of bias.
      </p>
    </div>
  );
}
