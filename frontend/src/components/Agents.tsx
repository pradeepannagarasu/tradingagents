import { fmt, pct } from "../api";

const PROV: Record<string, string> = {
  verified_historical_fact: "verified fact",
  current_market_observation: "observation",
  agent_interpretation: "interpretation",
  model_estimate: "model estimate",
  unverified_or_unavailable: "unverified / unavailable",
};

export function Evidence({ e }: { e: any }) {
  return (
    <details>
      <summary className="ev">
        <span className={`tag ${e.direction > 0 ? "up" : e.direction < 0 ? "down" : ""}`}>{e.category}</span>
        <span>{e.claim}</span>
        <span className="tag">{e.strength}</span>
      </summary>
      <dl className="kv small" style={{ marginTop: 8 }}>
        <dt>Evidence id</dt><dd className="mono">{e.id}</dd>
        <dt>Metric</dt><dd className="mono">{e.metric ?? "—"} = {fmt(e.value, 4)}</dd>
        <dt>Class</dt><dd>{PROV[e.provenance] ?? e.provenance}</dd>
        <dt>Source</dt><dd>{e.source?.url ? <a href={e.source.url} target="_blank" rel="noreferrer">{e.source.source}</a> : e.source?.source}{e.source?.simulated ? " (SIMULATED)" : ""}</dd>
        {e.source?.published_at && <><dt>Published</dt><dd>{e.source.published_at}</dd></>}
        <dt>As of</dt><dd>{e.as_of}</dd>
      </dl>
    </details>
  );
}

function Plan({ plan }: { plan: any }) {
  if (!plan) return <p className="muted small">No trade plan proposed.</p>;
  return (
    <>
      <dl className="kv">
        <dt>Instrument</dt><dd>{plan.instrument.symbol} ({plan.instrument.instrument_type})</dd>
        <dt>Direction</dt><dd>{plan.direction}</dd>
        <dt>Entry</dt><dd>{fmt(plan.entry_low)} – {fmt(plan.entry_high)}</dd>
        <dt>Stop-loss</dt><dd>{fmt(plan.stop_loss)}</dd>
        <dt>Targets</dt><dd>{fmt(plan.target_1)} / {fmt(plan.target_2)}</dd>
        <dt>R:R (T1)</dt><dd>{fmt(plan.risk_reward)}</dd>
        <dt>Holding</dt><dd>{plan.holding_period}</dd>
      </dl>
      {plan.notes?.length > 0 && <ul className="tight small muted">{plan.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>}
    </>
  );
}

export function AgentPanel({ a }: { a: any }) {
  const isBull = a.agent === "BULL";
  return (
    <div className={`card ${isBull ? "panel-bull" : "panel-bear"}`}>
      <h2>{a.agent} — {isBull ? "bullish research" : "bearish challenge"}</h2>
      <div className="row small" style={{ marginBottom: 8 }}>
        <span className="pill">stance {a.stance}</span>
        <span className="pill">conviction {pct(a.conviction, 0)}</span>
        {a.concedes && <span className="pill good">concedes</span>}
        <span className="pill">{a.narrative_source === "llm" ? a.model : "deterministic"} · {a.prompt_version}</span>
      </div>
      <p>{a.thesis}</p>
      <h3>{isBull ? "Supporting evidence" : "Risk evidence"} ({a.evidence.length})</h3>
      {a.evidence.map((e: any) => <Evidence key={e.id} e={e} />)}
      {a.challenges_to_other?.length > 0 && (<>
        <h3 style={{ marginTop: 12 }}>Why the bullish trade could fail</h3>
        <ul className="tight">{a.challenges_to_other.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul>
      </>)}
      <h3 style={{ marginTop: 12 }}>{isBull ? "Weaknesses in own case" : "Bullish points acknowledged"}</h3>
      {a.acknowledged_weaknesses.length ? a.acknowledged_weaknesses.map((e: any) => <Evidence key={e.id} e={e} />) : <p className="muted small">None.</p>}
      <h3 style={{ marginTop: 12 }}>{isBull ? "Proposed plan" : "Alternative"}</h3>
      {!isBull && a.alternative && <p>{a.alternative}</p>}
      <Plan plan={a.plan} />
      <h3 style={{ marginTop: 12 }}>Invalidation</h3>
      <ul className="tight small">{a.invalidation.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul>
      {a.rebuttals?.length > 0 && (<details><summary>Debate responses ({a.rebuttals.length})</summary>
        <ul className="tight small">{a.rebuttals.map((r: any, i: number) => <li key={i}><b>R{r.round} {r.response}</b> [{r.target_evidence_id}] {r.argument}</li>)}</ul>
      </details>)}
    </div>
  );
}

export function JudgePanel({ rec }: { rec: any }) {
  const j = rec.judge;
  return (
    <div className="card panel-judge">
      <h2>JUDGE — independent assessment</h2>
      <div className="row small" style={{ marginBottom: 8 }}>
        <span className="pill">winner: {j.winning_side}</span>
        <span className="pill">composite {fmt(j.composite_score, 3)} (not a probability)</span>
        <span className="pill">coverage {pct(j.coverage, 0)}</span>
        <span className="pill">disagreement {fmt(j.agent_disagreement, 2)}</span>
      </div>
      <ul className="tight">{j.reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
      <h3>Category scores</h3>
      <table><thead><tr><th>Category</th><th className="num">Weight</th><th>Score</th><th className="num">Verified</th><th className="num">Rejected</th></tr></thead>
        <tbody>{j.category_scores.map((c: any) => (
          <tr key={c.category}><td>{c.category}{!c.has_evidence && <span className="tag" style={{ marginLeft: 6 }}>no data</span>}</td>
            <td className="num">{c.weight}</td>
            <td><div className="bar" title={String(c.score)}><span style={{
              left: c.score >= 0 ? "50%" : `${50 + c.score * 50}%`, width: `${Math.abs(c.score) * 50}%`,
              background: c.score >= 0 ? "var(--bull)" : "var(--bear)" }} /></div></td>
            <td className="num">{c.verified_items}</td><td className="num">{c.rejected_items}</td></tr>))}</tbody></table>
      <h3 style={{ marginTop: 12 }}>Gates</h3>
      <table><tbody>{j.gates.map((g: any) => <tr key={g.name}><td>{g.passed ? "✓" : "✗"} {g.name}</td><td className="small muted">{g.detail}</td></tr>)}</tbody></table>
      <details open><summary>Strongest arguments</summary><ul className="tight small">{j.strongest_arguments.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></details>
      <details><summary>Rejected arguments ({j.rejected_arguments.length})</summary><ul className="tight small">{j.rejected_arguments.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></details>
      <details><summary>Key data points</summary><ul className="tight small">{j.key_data_points.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></details>
      <details><summary>Unresolved risks</summary><ul className="tight small">{j.unresolved_risks.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></details>
      <details><summary>What would change the decision</summary><ul className="tight small">{j.change_conditions.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></details>
      <details><summary>Debate transcript ({j.debate_summary.length})</summary><ul className="tight small">{j.debate_summary.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></details>
      <h3 style={{ marginTop: 12 }}>Confidence calibration</h3>
      <p className="small">{j.calibrated_probability != null ? `P(target 1 before stop) ≈ ${pct(j.calibrated_probability, 0)}. ` : ""}{j.calibration_note}</p>
      <h3>Risk engine</h3>
      {rec.risk ? (<>
        <p><span className={`pill ${rec.risk.approved ? "good" : "bad"}`}>{rec.risk.approved ? "APPROVED" : "REJECTED"}</span></p>
        <details><summary>Risk checks ({rec.risk.checks.length})</summary>
          <table><tbody>{rec.risk.checks.map((c: any) => <tr key={c.name}><td>{c.passed ? "✓" : "✗"} {c.name}</td><td className="small muted">{c.detail}</td></tr>)}</tbody></table></details>
      </>) : <p className="small muted">Not evaluated (no trade proposed by the judge).</p>}
    </div>
  );
}
