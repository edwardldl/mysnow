"use client";

import React from "react";
import { BrainCircuit } from "lucide-react";
import type { ModelForecastResult } from "@/lib/model-forecast";

interface ModelForecastPanelProps {
  result: ModelForecastResult | null;
}

const SOURCE_LABEL: Record<string, string> = {
  ecmwf: "ECMWF IFS",
  hrrr: "NOAA HRRR",
};

function formatCycle(cycleUtc: string | null): string {
  if (!cycleUtc) return "";
  const date = new Date(cycleUtc);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.toISOString().slice(0, 13).replace("T", " ")}Z cycle`;
}

function fmt(value: number | null, digits = 1): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

/**
 * Labelled model panel (design SS7): shown only at catalogue resort
 * points and only in ECMWF / HRRR source modes -- the gating lives in
 * lib/model-forecast.ts; this component renders nothing without a
 * resolved result. Model values sit alongside the same-window Kuchera
 * comparison and never replace the production figures.
 */
export default function ModelForecastPanel({ result }: ModelForecastPanelProps) {
  if (!result) return null;
  const { point, arm, cycleUtc, forecast } = result;

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-8 mt-4">
      <div className="glass-panel rounded-3xl border border-accent-violet/30 bg-accent-violet/5 p-5 md:p-6">
        <div className="flex flex-wrap items-center gap-2 md:gap-3">
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-accent-violet/20 border border-accent-violet/40 text-accent-violet text-[10px] font-black tracking-wider">
            <BrainCircuit className="w-3.5 h-3.5" />
            MODEL FORECAST
          </span>
          <span className="text-[11px] md:text-xs text-slate-300 font-semibold">
            Produced by the MySnow ENS-EB model — not the standard Kuchera estimate
          </span>
          <span className="text-[10px] md:text-[11px] text-slate-400 ml-auto">
            {SOURCE_LABEL[arm]} · {formatCycle(cycleUtc)}
          </span>
        </div>

        <div className="mt-4 flex flex-wrap items-end gap-x-8 gap-y-3">
          <div>
            <div className="text-[10px] font-black tracking-wider text-slate-400">
              MODEL SNOWFALL · {point.pointName.toUpperCase()} ({Math.round(point.elevationM)} m)
            </div>
            <div className="text-3xl md:text-4xl font-black text-white">
              {fmt(forecast.modelSnowfallCm)} <span className="text-base font-bold text-slate-300">cm</span>
            </div>
          </div>
          <div>
            <div className="text-[10px] font-black tracking-wider text-slate-400">MODEL SLR</div>
            <div className="text-xl md:text-2xl font-black text-slate-100">{fmt(forecast.modelSlr)} : 1</div>
          </div>
          <div>
            <div className="text-[10px] font-black tracking-wider text-slate-400">LIQUID (FORECAST)</div>
            <div className="text-xl md:text-2xl font-black text-slate-100">{fmt(forecast.liquidMm)} mm</div>
          </div>
          <div className="text-[11px] md:text-xs text-slate-400 leading-relaxed">
            Kuchera on the same forecast:{" "}
            <span className="text-slate-200 font-bold">{fmt(forecast.kucheraSnowfallCm)} cm</span>
            {" · "}SLR {fmt(forecast.kucheraSlr)} : 1
          </div>
        </div>

        <div className="mt-3 text-[10px] md:text-[11px] text-slate-500">
          24 h window ending 07:00 local · {point.resortName} · Experimental model output, shown for
          comparison alongside the production forecast
        </div>
      </div>
    </div>
  );
}
