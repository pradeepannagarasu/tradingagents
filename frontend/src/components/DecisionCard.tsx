import { useState } from "react";
import { api, decisionClass, fmt, inr } from "../api";
import { DataBadge } from "./DataBadge";

export function DecisionCard({ rec }: { rec: any }) {
  const j = rec.judge;
  const plan = j.plan;
  const risk = rec.risk;
  const [ticket, setTicket] = useState<any>(null);
  const [err, setErr] = useState("");
  const canTrade = rec.final_decision !== "NO TRADE" && risk?.approved;

  async function prepare() {
    setErr("");
    try { setTicket(await api("/api/orders/tickets", { decision_id: rec.id })); } catch (e: any) { setErr(e.message); }
  }
  return (
    <div className="card">
      <h2>Trade decision <DataBadge label={rec.data_quality.data_label ?? (rec.simulated_data ? "SIMULATED" : undefined)} /></h2>
      {rec.simulated_data && <div className="notice">SIMULATED DATA — this is a demonstration of the workflow, not a real market signal.</div>}
      <div className={`decision ${decisionClass(rec.final_decision)}`}>{rec.final_decision}</div>
      {rec.final_decision !== j.decision && <p className="small">Judge proposed <b>{j.decision}</b>; final decision changed by the risk engine.</p>}
      <dl className="kv" style={{ marginTop: 10 }}>
        <dt>Instrument</dt><dd>{rec.instrument.symbol} · {rec.instrument.exchange} · {rec.instrument.instrument_type}</dd>
        <dt>Direction</dt><dd>{plan?.direction ?? "FLAT"}</dd>
        <dt>Timeframe</dt><dd>{rec.horizon}</dd>
        <dt>Decision time</dt><dd>{rec.created_at}</dd>
        <dt>Market data as of</dt><dd>{rec.market_data_as_of}</dd>
        <dt>Entry range</dt><dd>{plan ? `${fmt(plan.entry_low)} – ${fmt(plan.entry_high)}` : "—"}</dd>
        <dt>Stop-loss</dt><dd>{fmt(plan?.stop_loss)}</dd>
        <dt>Target 1 / 2</dt><dd>{fmt(plan?.target_1)} / {fmt(plan?.target_2)}</dd>
        <dt>Risk / reward</dt><dd>{fmt(plan?.risk_reward)}</dd>
        <dt>Holding period</dt><dd>{plan?.holding_period ?? "—"}</dd>
        <dt>Position size</dt><dd>{risk?.approved ? `${risk.quantity} units` : "—"}</dd>
        <dt>Capital required</dt><dd>{risk?.approved ? inr(risk.capital_required) : "—"}</dd>
        <dt>Margin (est.)</dt><dd>{risk?.margin_required ? `${inr(risk.margin_required)} (estimate — broker SPAN is authoritative)` : "—"}</dd>
        <dt>Max loss at stop</dt><dd>{risk?.approved ? inr(risk.max_loss_at_stop) : "—"}</dd>
        <dt>Costs (est.)</dt><dd>{risk?.approved ? inr(risk.estimated_costs) : "—"}</dd>
        <dt>Slippage (est.)</dt><dd>{risk?.approved ? inr(risk.estimated_slippage) : "—"}</dd>
        <dt>Agent disagreement</dt><dd>{fmt(j.agent_disagreement, 2)}</dd>
        <dt>Data quality</dt><dd>{rec.data_quality.status}{rec.data_quality.stale ? " (stale)" : ""}</dd>
        <dt>Risk engine</dt><dd>{risk ? (risk.approved ? "APPROVED" : "REJECTED") : "not applicable"}</dd>
        <dt>Snapshot hash</dt><dd className="mono">{rec.snapshot_hash.slice(0, 16)}…</dd>
      </dl>
      {plan?.notes?.length > 0 && <ul className="tight small muted">{plan.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>}
      <h3 style={{ marginTop: 10 }}>Reasons</h3>
      <ul className="tight small">{j.reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
      {plan && <><h3>Key invalidation</h3><ul className="tight small">{plan.invalidation.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul></>}
      <p className="small muted">{rec.disclaimer}</p>
      {canTrade && <button className="btn" onClick={prepare}>Prepare order ticket…</button>}
      {err && <div className="error">{err}</div>}
      {ticket && <ApprovalModal ticket={ticket} onClose={() => setTicket(null)} />}
    </div>
  );
}

function ApprovalModal({ ticket, onClose }: { ticket: any; onClose: () => void }) {
  const t = ticket.ticket_json;
  const [typed, setTyped] = useState("");
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState("");
  async function confirm() {
    setErr("");
    try { setResult(await api(`/api/orders/tickets/${ticket.id}/confirm`, { confirmation: typed, ticket_hash: ticket.ticket_hash })); }
    catch (e: any) { setErr(e.message); }
  }
  async function reject() { await api(`/api/orders/tickets/${ticket.id}/reject`, {}); onClose(); }
  return (
    <div className="modal-back" role="dialog" aria-modal="true">
      <div className="modal">
        <h3>Order approval — {ticket.mode}</h3>
        {t.simulated_data && <div className="notice">Based on SIMULATED data — paper execution only.</div>}
        <dl className="kv">
          <dt>Broker</dt><dd>{t.broker}</dd><dt>Account</dt><dd>{t.account}</dd>
          <dt>Instrument</dt><dd>{t.instrument} ({t.exchange}, {t.instrument_type})</dd>
          <dt>Direction</dt><dd>{t.side} ({t.direction})</dd><dt>Quantity</dt><dd>{t.quantity}</dd>
          <dt>Order type</dt><dd>{t.order_type} @ {fmt(t.limit_price)} · product {t.product}</dd>
          <dt>Stop-loss</dt><dd>{fmt(t.stop_loss)}</dd>
          <dt>Capital / margin</dt><dd>{inr(t.capital_required)}{t.margin_required ? ` (margin est. ${inr(t.margin_required)})` : ""}</dd>
          <dt>Max loss at stop</dt><dd>{inr(t.max_loss_at_stop)}</dd>
          <dt>Costs + slippage</dt><dd>{inr(t.estimated_costs)} + {inr(t.estimated_slippage)}</dd>
          <dt>Risk engine</dt><dd>{t.risk_approved ? "APPROVED" : "REJECTED"}</dd>
        </dl>
        <h3 style={{ marginTop: 10 }}>Judge's reasoning</h3>
        <ul className="tight small">{t.judge_reasoning.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
        {t.price_notes?.length > 0 && <ul className="tight small muted">{t.price_notes.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>}
        <p className="small mono">ticket hash {ticket.ticket_hash.slice(0, 24)}…</p>
        {result ? (<p>Order status: <b>{result.status}</b> {result.broker_order_id && `(${result.broker_order_id})`}</p>) : (<>
          <label className="small">Type <b>{ticket.confirm_phrase}</b> to submit:</label>
          <div className="row" style={{ marginTop: 6 }}>
            <input value={typed} onChange={e => setTyped(e.target.value)} placeholder={ticket.confirm_phrase} />
            <button className="btn" disabled={typed !== ticket.confirm_phrase} onClick={confirm}>CONFIRM TRADE</button>
            <button className="btn secondary" onClick={reject}>Reject</button>
          </div></>)}
        {err && <div className="error">{err}</div>}
        <div className="row" style={{ marginTop: 12 }}><button className="btn secondary" onClick={onClose}>Close</button></div>
      </div>
    </div>
  );
}
