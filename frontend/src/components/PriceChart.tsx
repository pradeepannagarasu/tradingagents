import { useEffect, useRef } from "react";
import { createChart, ColorType, IChartApi, LineStyle } from "lightweight-charts";

type Bar = { time: string; open: number; high: number; low: number; close: number; sma_50: number | null; sma_200: number | null };
type Level = { price: number; label: string; color: string };

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

// Charts by TradingView Lightweight Charts™ (Apache-2.0) — attribution required by its licence.
export function PriceChart({ bars, levels = [] }: { bars: Bar[]; levels?: Level[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !bars.length) return;
    const chart: IChartApi = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: cssVar("--muted") },
      grid: { vertLines: { color: cssVar("--border") }, horzLines: { color: cssVar("--border") } },
      rightPriceScale: { borderColor: cssVar("--border") }, timeScale: { borderColor: cssVar("--border") },
    });
    const candles = chart.addCandlestickSeries({
      upColor: cssVar("--bull"), downColor: cssVar("--bear"), borderVisible: false,
      wickUpColor: cssVar("--bull"), wickDownColor: cssVar("--bear"),
    });
    candles.setData(bars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
    for (const [key, color] of [["sma_50", cssVar("--accent")], ["sma_200", cssVar("--warn")]] as const) {
      const s = chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      s.setData(bars.filter(b => b[key] !== null).map(b => ({ time: b.time, value: b[key] as number })));
    }
    for (const l of levels) {
      if (l.price) candles.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: l.label });
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, levels]);
  return (
    <div>
      <div ref={ref} className="chart" />
      <div className="small muted">SMA50 (blue) · SMA200 (amber) · Charts: TradingView Lightweight Charts™</div>
    </div>
  );
}
