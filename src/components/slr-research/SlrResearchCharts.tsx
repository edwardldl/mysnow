"use client";

import type { MethodId, RegionView } from "@/lib/slr-research-data";
import { METHOD_META } from "@/lib/slr-research-data";

const fmt = (value: number, digits = 2) => value.toFixed(digits);

export function MetricDumbbellChart({ region }: { region: RegionView }) {
  const rows = [
    { label: "MAE", baseline: region.baseline.mae, prediction: region.prediction.mae },
    { label: "RMSE", baseline: region.baseline.rmse, prediction: region.prediction.rmse },
  ];
  const max = Math.max(...rows.flatMap((row) => [row.baseline, row.prediction])) * 1.12;
  const x = (value: number) => 122 + (value / max) * 458;

  return (
    <div className="overflow-hidden rounded-2xl border border-white/10 bg-slate-950/55 p-3 sm:p-5">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-bold text-white">Error distance</h3>
          <p className="text-[11px] text-slate-400">Lower is better · exact holdout values</p>
        </div>
        <ChartLegend />
      </div>
      <svg viewBox="0 0 640 190" role="img" aria-labelledby={`dumbbell-${region.id}`} className="h-auto w-full min-w-[480px]">
        <title id={`dumbbell-${region.id}`}>{`${region.name} baseline and profile-aware MAE and RMSE comparison`}</title>
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
          <g key={tick}>
            <line x1={122 + tick * 458} y1="28" x2={122 + tick * 458} y2="150" stroke="rgba(148,163,184,.12)" />
            <text x={122 + tick * 458} y="174" textAnchor="middle" fill="#64748b" fontSize="11">{fmt(max * tick, 1)}</text>
          </g>
        ))}
        {rows.map((row, index) => {
          const y = 65 + index * 66;
          return (
            <g key={row.label}>
              <text x="12" y={y + 4} fill="#cbd5e1" fontSize="13" fontWeight="700">{row.label}</text>
              <line x1={x(row.prediction)} y1={y} x2={x(row.baseline)} y2={y} stroke="#334155" strokeWidth="6" strokeLinecap="round" />
              <circle cx={x(row.baseline)} cy={y} r="9" fill={METHOD_META.baseline.color}>
                <title>{`Kuchera ${row.label}: ${fmt(row.baseline, 3)}`}</title>
              </circle>
              <circle cx={x(row.prediction)} cy={y} r="10" fill={METHOD_META.ridgeFull.color} stroke="#cffafe" strokeWidth="2">
                <title>{`Full ridge ${row.label}: ${fmt(row.prediction, 3)}`}</title>
              </circle>
              <text x={x(row.baseline)} y={y - 17} textAnchor="middle" fill="#94a3b8" fontSize="11">{fmt(row.baseline)}</text>
              <text x={x(row.prediction)} y={y + 27} textAnchor="middle" fill="#67e8f9" fontSize="11" fontWeight="700">{fmt(row.prediction)}</text>
            </g>
          );
        })}
      </svg>
      <p className="sr-only">MAE improves from {fmt(region.baseline.mae, 3)} to {fmt(region.prediction.mae, 3)}. RMSE improves from {fmt(region.baseline.rmse, 3)} to {fmt(region.prediction.rmse, 3)}.</p>
    </div>
  );
}

function ChartLegend() {
  return (
    <div className="flex items-center gap-3 text-[10px] font-semibold text-slate-400">
      <span className="flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-full bg-slate-400" />Kuchera</span>
      <span className="flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-full bg-cyan-400 ring-2 ring-cyan-100/30" />Full ridge</span>
    </div>
  );
}

export function BiasChart({ region }: { region: RegionView }) {
  const methods: MethodId[] = ["baseline", "intercept", "ridgeThermal", "ridgeFull", "globalPrior"];
  const maxAbs = Math.max(...methods.map((method) => Math.abs(region.methods[method].bias)), 0.5) * 1.18;
  const x = (value: number) => 320 + (value / maxAbs) * 245;

  return (
    <div className="overflow-hidden rounded-2xl border border-white/10 bg-slate-950/55 p-3 sm:p-5">
      <div className="mb-1">
        <h3 className="text-sm font-bold text-white">Signed bias</h3>
        <p className="text-[11px] text-slate-400">Centered on zero · negative means underprediction</p>
      </div>
      <svg viewBox="0 0 640 250" role="img" aria-labelledby={`bias-${region.id}`} className="h-auto w-full min-w-[500px]">
        <title id={`bias-${region.id}`}>{`${region.name} signed bias by method`}</title>
        <rect x="75" y="18" width="490" height="208" rx="12" fill="rgba(15,23,42,.42)" />
        <line x1="320" y1="18" x2="320" y2="226" stroke="#e2e8f0" strokeWidth="1.5" />
        <text x="310" y="244" textAnchor="end" fill="#64748b" fontSize="10">underprediction</text>
        <text x="330" y="244" fill="#64748b" fontSize="10">overprediction</text>
        {methods.map((method, index) => {
          const value = region.methods[method].bias;
          const y = 48 + index * 38;
          const start = Math.min(320, x(value));
          const width = Math.max(2, Math.abs(x(value) - 320));
          return (
            <g key={method}>
              <text x="87" y={y + 4} fill="#cbd5e1" fontSize="11">{METHOD_META[method].shortLabel}</text>
              <rect x={start} y={y - 7} width={width} height="14" rx="7" fill={METHOD_META[method].color} opacity={method === "ridgeFull" ? 1 : 0.72}>
                <title>{`${METHOD_META[method].label}: bias ${fmt(value, 3)}`}</title>
              </rect>
              <circle cx={x(value)} cy={y} r={method === "ridgeFull" ? 5 : 4} fill={METHOD_META[method].color} />
              <text x={value >= 0 ? x(value) + 8 : x(value) - 8} y={y + 4} textAnchor={value >= 0 ? "start" : "end"} fill={method === "ridgeFull" ? "#cffafe" : "#94a3b8"} fontSize="10" fontWeight={method === "ridgeFull" ? "700" : "500"}>{value > 0 ? "+" : ""}{fmt(value)}</text>
            </g>
          );
        })}
      </svg>
      {!region.biasMagnitudeImproved && (
        <div className="rounded-xl border border-amber-300/20 bg-amber-300/[0.06] px-3 py-2 text-[11px] leading-relaxed text-amber-100">
          Honest trade-off: absolute bias worsens by {fmt(Math.abs(region.absoluteBiasChange), 3)} even while MAE and RMSE improve.
        </div>
      )}
    </div>
  );
}

export function ImprovementBars({ regions }: { regions: RegionView[] }) {
  const max = Math.max(...regions.map((region) => region.maeImprovementPct)) * 1.1;
  return (
    <div className="space-y-4" role="img" aria-label="Percentage MAE improvement by region">
      {regions.map((region) => (
        <div key={region.id} className="grid grid-cols-[90px_1fr_56px] items-center gap-3 sm:grid-cols-[120px_1fr_70px]">
          <span className="truncate text-xs font-semibold text-slate-300">{region.shortName}</span>
          <div className="h-3 overflow-hidden rounded-full bg-slate-800/90">
            <div className="h-full rounded-full bg-gradient-to-r from-blue-500 to-cyan-300" style={{ width: `${(region.maeImprovementPct / max) * 100}%` }} />
          </div>
          <span className="text-right font-mono text-xs font-bold text-cyan-200">{fmt(region.maeImprovementPct, 1)}%</span>
        </div>
      ))}
    </div>
  );
}

export function ForestPlot({ regions }: { regions: RegionView[] }) {
  const intervalValues = regions.flatMap((region) => [
    ...region.intervals.station.delta,
    ...region.intervals.storm.delta,
  ]);
  const min = Math.min(...intervalValues, -3.5);
  const max = Math.max(...intervalValues, 0.5);
  const x = (value: number) => 160 + ((value - min) / (max - min)) * 430;

  return (
    <div className="overflow-x-auto rounded-2xl border border-white/10 bg-slate-950/55 p-3 sm:p-5">
      <svg viewBox="0 0 640 390" role="img" aria-labelledby="forest-title" className="h-auto min-w-[560px] w-full">
        <title id="forest-title">Station and storm cluster bootstrap 95 percent intervals for MAE difference between full ridge and Kuchera</title>
        <rect x="160" y="28" width={x(0) - 160} height="310" fill="rgba(34,211,238,.045)" />
        <line x1={x(0)} y1="28" x2={x(0)} y2="338" stroke="#fbbf24" strokeWidth="2" strokeDasharray="5 5" />
        <text x={x(0)} y="18" textAnchor="middle" fill="#fcd34d" fontSize="10">zero · no difference</text>
        <text x="160" y="364" fill="#67e8f9" fontSize="10">← full ridge lower MAE</text>
        <text x="590" y="364" textAnchor="end" fill="#fca5a5" fontSize="10">Kuchera lower MAE →</text>
        {regions.map((region, regionIndex) => {
          const baseY = 72 + regionIndex * 76;
          return (
            <g key={region.id}>
              <text x="12" y={baseY + 10} fill="#e2e8f0" fontSize="12" fontWeight="700">{region.shortName}</text>
              {(["station", "storm"] as const).map((cluster, clusterIndex) => {
                const y = baseY + clusterIndex * 25;
                const [lo, hi] = region.intervals[cluster].delta;
                const center = -(region.maeImprovement);
                return (
                  <g key={cluster}>
                    <text x="92" y={y + 4} fill="#64748b" fontSize="9">{cluster}</text>
                    <line x1={x(lo)} x2={x(hi)} y1={y} y2={y} stroke={cluster === "station" ? "#38bdf8" : "#a78bfa"} strokeWidth="4" strokeLinecap="round">
                      <title>{`${region.name}, ${cluster}-cluster: [${fmt(lo, 3)}, ${fmt(hi, 3)}]`}</title>
                    </line>
                    <line x1={x(lo)} x2={x(lo)} y1={y - 5} y2={y + 5} stroke="#e2e8f0" />
                    <line x1={x(hi)} x2={x(hi)} y1={y - 5} y2={y + 5} stroke="#e2e8f0" />
                    <circle cx={x(center)} cy={y} r="4" fill={cluster === "station" ? "#38bdf8" : "#a78bfa"} />
                    <text x={Math.min(x(hi) + 7, 602)} y={y + 4} fill="#94a3b8" fontSize="9">[{fmt(lo)}, {fmt(hi)}]</text>
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      <p className="mt-2 text-[11px] leading-relaxed text-slate-400">Every interval remains left of zero under both cluster definitions. These intervals describe sampling variation within the observed station or storm clusters—not universal model certainty.</p>
    </div>
  );
}

interface ResidualSummary {
  mean: number;
  median: number;
  stationMeanRange: [number, number];
  stationMeans: Record<string, number>;
}

export function ResidualShiftChart({ train, test }: { train: ResidualSummary; test: ResidualSummary }) {
  const all = [...Object.values(train.stationMeans), ...Object.values(test.stationMeans), train.mean, test.mean];
  const min = Math.min(...all, -0.2) - 0.03;
  const max = Math.max(...all, 0.3) + 0.03;
  const x = (value: number) => 130 + ((value - min) / (max - min)) * 430;
  const groups = [
    { label: "Train", data: train, y: 78, color: "#94a3b8" },
    { label: "Untouched test", data: test, y: 165, color: "#f472b6" },
  ];

  return (
    <div className="overflow-x-auto rounded-2xl border border-white/10 bg-slate-950/55 p-3 sm:p-5">
      <svg viewBox="0 0 640 250" role="img" aria-labelledby="residual-title" className="h-auto min-w-[540px] w-full">
        <title id="residual-title">Northeast training and untouched test station mean log residuals</title>
        <line x1={x(0)} y1="28" x2={x(0)} y2="210" stroke="#f8fafc" strokeWidth="1.5" strokeDasharray="4 4" />
        <text x={x(0)} y="20" textAnchor="middle" fill="#cbd5e1" fontSize="10">zero residual</text>
        {groups.map((group) => (
          <g key={group.label}>
            <text x="10" y={group.y + 5} fill="#e2e8f0" fontSize="12" fontWeight="700">{group.label}</text>
            <line x1={x(group.data.stationMeanRange[0])} x2={x(group.data.stationMeanRange[1])} y1={group.y} y2={group.y} stroke={group.color} strokeWidth="4" strokeLinecap="round" />
            {Object.entries(group.data.stationMeans).map(([station, value], index) => (
              <circle key={station} cx={x(value)} cy={group.y + ((index % 2) * 12 - 6)} r="5" fill={group.color} opacity=".8">
                <title>{`${station}: ${fmt(value, 3)}`}</title>
              </circle>
            ))}
            <polygon points={`${x(group.data.mean)},${group.y - 12} ${x(group.data.mean) + 8},${group.y} ${x(group.data.mean)},${group.y + 12} ${x(group.data.mean) - 8},${group.y}`} fill="#fff" stroke={group.color} strokeWidth="2">
              <title>{`${group.label} aggregate mean: ${fmt(group.data.mean, 3)}`}</title>
            </polygon>
            <text x={x(group.data.mean)} y={group.y + 31} textAnchor="middle" fill={group.color} fontSize="10" fontWeight="700">mean {group.data.mean > 0 ? "+" : ""}{fmt(group.data.mean, 3)}</text>
          </g>
        ))}
        <text x="130" y="232" fill="#64748b" fontSize="10">Kuchera predicts too high ←</text>
        <text x="560" y="232" textAnchor="end" fill="#64748b" fontSize="10">→ Kuchera predicts too low</text>
      </svg>
    </div>
  );
}

export function GlobalPriorChart({ regions }: { regions: RegionView[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {regions.map((region) => {
        const baseline = region.methods.baseline.mae;
        const prior = region.methods.globalPrior.mae;
        const improved = prior < baseline;
        return (
          <div key={region.id} className={`rounded-2xl border p-4 ${improved ? "border-white/10 bg-white/[0.025]" : "border-rose-400/30 bg-rose-400/[0.06]"}`}>
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-xs font-bold text-white">{region.shortName}</h3>
              <span className={`rounded-full px-2 py-1 text-[9px] font-black uppercase tracking-wider ${improved ? "bg-cyan-400/10 text-cyan-200" : "bg-rose-400/15 text-rose-200"}`}>{improved ? "improved" : "worsened"}</span>
            </div>
            <div className="mt-4 flex items-end gap-2 font-mono">
              <span className="text-xl font-bold text-slate-400">{fmt(baseline, 3)}</span>
              <span className="pb-1 text-slate-600">→</span>
              <span className={`text-xl font-black ${improved ? "text-cyan-200" : "text-rose-200"}`}>{fmt(prior, 3)}</span>
            </div>
            <p className="mt-2 text-[10px] text-slate-500">MAE · pooled offset from other regions</p>
          </div>
        );
      })}
    </div>
  );
}
