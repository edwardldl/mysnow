"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  BookOpen,
  Braces,
  CheckCircle2,
  ChevronDown,
  CircleSlash2,
  Database,
  ExternalLink,
  FlaskConical,
  GitBranch,
  Info,
  Layers3,
  LockKeyhole,
  MountainSnow,
  Scale,
  ShieldAlert,
  Snowflake,
  Sparkles,
  Table2,
  Target,
  ThermometerSnowflake,
} from "lucide-react";
import type { RegionId, RegionView, SlrResearchViewModel } from "@/lib/slr-research-data";
import { METHOD_META, METHOD_ORDER, REGION_ORDER } from "@/lib/slr-research-data";
import {
  BiasChart,
  ForestPlot,
  GlobalPriorChart,
  ImprovementBars,
  MetricDumbbellChart,
  ResidualShiftChart,
} from "./SlrResearchCharts";

const compact = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
const precise = new Intl.NumberFormat("en-US", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const signed = new Intl.NumberFormat("en-US", { minimumFractionDigits: 3, maximumFractionDigits: 3, signDisplay: "exceptZero" });

function format(value: number): string {
  return precise.format(value);
}

function pct(value: number): string {
  return `${compact.format(value)}%`;
}

const sectionLinks = [
  ["overview", "Overview"],
  ["region-detail", "Regional detail"],
  ["uncertainty", "Uncertainty"],
  ["slices", "Slices"],
  ["methods", "Methods"],
  ["northeast", "Northeast"],
  ["coverage", "Coverage gaps"],
  ["science", "Science"],
] as const;

export default function SlrResearchDashboard({ data }: { data: SlrResearchViewModel }) {
  const [selectedRegionId, setSelectedRegionId] = useState<RegionId>("rockies");

  useEffect(() => {
    const query = new URLSearchParams(window.location.search).get("region") as RegionId | null;
    const hash = window.location.hash.startsWith("#region-")
      ? window.location.hash.slice("#region-".length) as RegionId
      : null;
    const requested = hash ?? query;
    if (!requested || !REGION_ORDER.includes(requested)) return;
    const timer = window.setTimeout(() => setSelectedRegionId(requested), 0);
    return () => window.clearTimeout(timer);
  }, []);

  const selectRegion = (id: RegionId) => {
    setSelectedRegionId(id);
    const url = new URL(window.location.href);
    url.searchParams.delete("region");
    window.history.replaceState({}, "", `${url.pathname}${url.search}#region-${id}`);
  };

  const selectedRegion = data.regions.find((region) => region.id === selectedRegionId) ?? data.regions[0];

  return (
    <main className="min-h-screen overflow-x-hidden bg-[#020617] text-slate-100">
      <div className="pointer-events-none fixed inset-x-0 top-0 h-[620px] bg-[radial-gradient(circle_at_20%_10%,rgba(14,165,233,.16),transparent_38%),radial-gradient(circle_at_85%_18%,rgba(139,92,246,.13),transparent_34%)]" />

      <ResearchHeader />

      <div className="relative mx-auto max-w-[1440px] px-4 pb-20 pt-8 sm:px-6 lg:px-10">
        <Hero data={data} />

        <nav aria-label="Research sections" className="sticky top-[61px] z-30 -mx-4 mt-8 border-y border-white/[0.07] bg-slate-950/85 px-4 py-2 backdrop-blur-xl sm:-mx-6 sm:px-6 lg:-mx-10 lg:px-10">
          <div className="no-scrollbar mx-auto flex max-w-[1360px] gap-1 overflow-x-auto">
            {sectionLinks.map(([id, label]) => (
              <a key={id} href={`#${id}`} className="shrink-0 rounded-lg px-3 py-2 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500 transition hover:bg-white/5 hover:text-white focus:outline-none focus:ring-2 focus:ring-cyan-400/50">
                {label}
              </a>
            ))}
          </div>
        </nav>

        <section id="overview" className="scroll-mt-32 pt-14">
          <SectionHeading
            eyebrow="Fixed held-out evaluation"
            title="Four regions, one honest comparison"
            description="Every card compares the exact MySnow Kuchera baseline with the profile-aware full-feature ridge on one untouched holdout. Positive MAE and RMSE reductions are good; bias is signed and judged by absolute magnitude."
            icon={BarChart3}
          />
          <div className="mt-7 grid gap-4 xl:grid-cols-4">
            {data.regions.map((region) => (
              <RegionOverviewCard key={region.id} region={region} active={region.id === selectedRegionId} onSelect={() => selectRegion(region.id)} />
            ))}
          </div>
          <div className="mt-5 grid gap-4 lg:grid-cols-[1.4fr_.8fr]">
            <Panel>
              <PanelTitle title="MAE reduction" description="Relative improvement from original Kuchera to the full profile-aware ridge." />
              <div className="mt-6"><ImprovementBars regions={data.regions} /></div>
            </Panel>
            <Panel className="relative overflow-hidden">
              <div className="absolute -right-12 -top-12 h-36 w-36 rounded-full bg-cyan-400/10 blur-3xl" />
              <PanelTitle title="What the aggregate says" description="All four fixed holdouts improve in MAE and RMSE." />
              <dl className="mt-6 grid grid-cols-2 gap-4">
                <Stat label="Held-out events" value={String(data.regions.reduce((sum, region) => sum + region.holdout.testEvents, 0))} />
                <Stat label="Unseen stations" value={String(data.regions.reduce((sum, region) => sum + region.holdout.testStations, 0))} />
                <Stat label="Complete storms" value={String(data.regions.reduce((sum, region) => sum + region.holdout.testStorms, 0))} />
                <Stat label="Regions improved" value="4 / 4" accent />
              </dl>
              <p className="mt-5 text-[11px] leading-relaxed text-slate-400">This is not deployment evidence. Small regional holdouts, unresolved U.S. label authorization, and profile-source licensing keep the candidate research-only.</p>
            </Panel>
          </div>
        </section>

        <section id="region-detail" className="scroll-mt-32 pt-20">
          <SectionHeading
            eyebrow="Synchronized regional explorer"
            title="Inspect one region at a time"
            description="Tabs update every chart, table and slice below. The selected region is encoded in the URL for sharing."
            icon={MountainSnow}
          />
          <RegionTabs regions={data.regions} selected={selectedRegionId} onSelect={selectRegion} />
          <div className="mt-5 rounded-[28px] border border-white/10 bg-white/[0.025] p-4 shadow-2xl shadow-black/20 sm:p-6 lg:p-8">
            <div className="flex flex-col gap-5 border-b border-white/[0.07] pb-6 lg:flex-row lg:items-end lg:justify-between">
              <div>
                <p className="text-[10px] font-black uppercase tracking-[0.24em]" style={{ color: selectedRegion.accent }}>{selectedRegion.eyebrow}</p>
                <h2 className="mt-2 text-2xl font-black tracking-tight text-white sm:text-3xl">{selectedRegion.name}</h2>
                <p className="mt-2 max-w-3xl text-sm leading-relaxed text-slate-400">{selectedRegion.description}</p>
              </div>
              <div className="grid grid-cols-3 gap-2 sm:min-w-[390px]">
                <MiniStat label="Events" value={selectedRegion.holdout.testEvents} />
                <MiniStat label="Stations" value={selectedRegion.holdout.testStations} />
                <MiniStat label="Storms" value={selectedRegion.holdout.testStorms} />
              </div>
            </div>

            <div className="mt-6 grid gap-4 sm:grid-cols-3">
              <MetricComparison label="Mean absolute error" baseline={selectedRegion.baseline.mae} prediction={selectedRegion.prediction.mae} change={selectedRegion.maeImprovementPct} />
              <MetricComparison label="Root mean square error" baseline={selectedRegion.baseline.rmse} prediction={selectedRegion.prediction.rmse} change={selectedRegion.rmseImprovementPct} />
              <MetricComparison
                label="Signed bias"
                baseline={selectedRegion.baseline.bias}
                prediction={selectedRegion.prediction.bias}
                change={selectedRegion.absoluteBiasChange}
                signedMetric
                warning={!selectedRegion.biasMagnitudeImproved}
              />
            </div>

            <div className="mt-4 grid gap-4 xl:grid-cols-2">
              <MetricDumbbellChart region={selectedRegion} />
              <BiasChart region={selectedRegion} />
            </div>

            <div className="mt-5 flex flex-wrap gap-2 text-[10px] text-slate-400">
              <Pill>{selectedRegion.holdout.splitLabel}</Pill>
              <Pill>{selectedRegion.holdout.trainEvents} training events</Pill>
              <Pill>{selectedRegion.holdout.trainStations} training stations</Pill>
              <Pill>{selectedRegion.holdout.trainStorms} training storms</Pill>
              <Pill>evaluated once</Pill>
            </div>
          </div>
        </section>

        <section id="uncertainty" className="scroll-mt-32 pt-20">
          <SectionHeading
            eyebrow="Cluster-aware sampling uncertainty"
            title="The improvement survives two cluster definitions"
            description="Intervals show MAE(full ridge) − MAE(Kuchera). Negative values favor the profile-aware model; zero means no difference. Each interval uses 2,000 deterministic bootstrap replicates."
            icon={Scale}
          />
          <div className="mt-7"><ForestPlot regions={data.regions} /></div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {data.regions.map((region) => (
              <div key={region.id} className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
                <h3 className="text-xs font-bold text-white">{region.shortName}</h3>
                <div className="mt-3 space-y-2 font-mono text-[10px] text-slate-400">
                  <div className="flex justify-between gap-2"><span>station · n={region.intervals.station.clusters}</span><span className="text-cyan-200">[{format(region.intervals.station.delta[0])}, {format(region.intervals.station.delta[1])}]</span></div>
                  <div className="flex justify-between gap-2"><span>storm · n={region.intervals.storm.clusters}</span><span className="text-violet-200">[{format(region.intervals.storm.delta[0])}, {format(region.intervals.storm.delta[1])}]</span></div>
                </div>
              </div>
            ))}
          </div>
          <Callout tone="neutral" icon={Info} title="What these intervals do—and do not—mean">
            They resample observed stations or storms as clusters and describe sampling variation inside these fixed holdouts. They are not confidence intervals for worldwide performance, do not establish exchangeability, and are not formal conformal guarantees.
          </Callout>
        </section>

        <section id="slices" className="scroll-mt-32 pt-20">
          <SectionHeading
            eyebrow={`${selectedRegion.name} · slice audit`}
            title="Aggregate gains can hide weak slices"
            description="Counts summarize where full-ridge MAE is strictly lower than Kuchera. Expand each table for canonical event counts and method-by-method metrics."
            icon={Table2}
          />
          <div className="mt-7 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {selectedRegion.slices.map((dimension) => (
              <div key={dimension.id} className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">{dimension.label}</p>
                <div className="mt-3 flex items-baseline gap-2"><span className="text-3xl font-black text-white">{dimension.improved}</span><span className="text-sm text-slate-500">/ {dimension.total} improved</span></div>
                <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-800"><div className="h-full rounded-full bg-gradient-to-r from-blue-500 to-cyan-300" style={{ width: `${(dimension.improved / Math.max(dimension.total, 1)) * 100}%` }} /></div>
              </div>
            ))}
          </div>
          <div className="mt-5 space-y-3">
            {selectedRegion.slices.map((dimension, index) => (
              <SliceTable key={`${selectedRegion.id}-${dimension.id}`} dimension={dimension} defaultOpen={index === 0} />
            ))}
          </div>
        </section>

        <section id="methods" className="scroll-mt-32 pt-20">
          <SectionHeading
            eyebrow="Ablation and selection"
            title="What each modeling layer adds"
            description="Shared settings were selected only on Rockies, Sierra and Swiss training partitions using unseen-station and complete-storm grouped cross-validation. Northeast was excluded from every selection decision."
            icon={Layers3}
          />
          <div className="mt-7 overflow-hidden rounded-2xl border border-white/10 bg-slate-950/55">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[800px] text-left text-xs">
                <thead className="border-b border-white/10 bg-white/[0.03] text-[9px] uppercase tracking-[0.14em] text-slate-500">
                  <tr><th className="px-4 py-3">Method</th><th className="px-4 py-3">What it adds</th>{data.regions.map((region) => <th key={region.id} className="px-4 py-3 text-right">{region.shortName} MAE</th>)}</tr>
                </thead>
                <tbody>
                  {METHOD_ORDER.map((method) => (
                    <tr key={method} className={`border-b border-white/[0.05] last:border-0 ${method === "ridgeFull" ? "bg-cyan-400/[0.035]" : ""}`}>
                      <td className="px-4 py-4"><div className="flex items-center gap-2"><i className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: METHOD_META[method].color }} /><span className="font-bold text-white">{METHOD_META[method].label}</span></div></td>
                      <td className="max-w-sm px-4 py-4 leading-relaxed text-slate-400">{METHOD_META[method].description}</td>
                      {data.regions.map((region) => {
                        const value = region.methods[method].mae;
                        const best = value === Math.min(...METHOD_ORDER.map((candidate) => region.methods[candidate].mae));
                        return <td key={region.id} className={`px-4 py-4 text-right font-mono ${best ? "font-black text-cyan-200" : "text-slate-300"}`}>{format(value)}</td>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="mt-5 grid gap-4 lg:grid-cols-3">
            {(["intercept", "ridgeThermal", "ridgeFull"] as const).map((method) => {
              const selected = data.selectedModels[method];
              const parameter = "alpha" in selected && selected.alpha !== undefined ? `α ${selected.alpha}` : `shrinkage ${selected.shrinkage}`;
              return (
                <Panel key={method}>
                  <div className="flex items-start justify-between gap-3">
                    <div><p className="text-[10px] font-black uppercase tracking-[0.16em] text-slate-500">Selected by grouped CV</p><h3 className="mt-2 text-base font-bold text-white">{METHOD_META[method].label}</h3></div>
                    <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 font-mono text-[10px] text-slate-300">{parameter}</span>
                  </div>
                  <dl className="mt-5 grid grid-cols-2 gap-3">
                    <Stat label="Balance" value={selected.balance} />
                    <Stat label="Macro CV MAE" value={format(selected.macroCvMae)} accent={method === "ridgeFull"} />
                  </dl>
                </Panel>
              );
            })}
          </div>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Panel><PanelTitle title="Fold family: unseen station" description={data.selection.foldFamilies.unseen_station} /><p className="mt-4 text-xs leading-relaxed text-slate-400">Tests spatial transfer by excluding every event from one station. It prevents local station behavior from leaking into its own evaluation.</p></Panel>
            <Panel><PanelTitle title="Fold family: contiguous storm" description={data.selection.foldFamilies.contiguous_storm} /><p className="mt-4 text-xs leading-relaxed text-slate-400">Holds out complete chronological storm blocks so correlated observations from one storm do not straddle train and validation.</p></Panel>
          </div>
        </section>

        <section id="northeast" className="scroll-mt-32 pt-20">
          <SectionHeading
            eyebrow="The failure that taught us the most"
            title="Why the original Northeast correction failed"
            description="A single broad intercept could not represent a holdout whose aggregate residual changed sign and whose stations disagreed with one another."
            icon={GitBranch}
          />
          <div className="mt-7 grid gap-4 xl:grid-cols-[1.35fr_.65fr]">
            <ResidualShiftChart train={data.northeast.rootCauseDiagnostic.trainLogObservedOverBaseline} test={data.northeast.rootCauseDiagnostic.testLogObservedOverBaseline} />
            <Panel>
              <PanelTitle title="Support path at inference" description="What the old hierarchy actually supplied to the 80 held-out events." />
              <div className="mt-6 space-y-5">
                {Object.entries(data.northeast.rootCauseDiagnostic.existingEvaluationHierarchyCounts).map(([path, count]) => (
                  <div key={path}>
                    <div className="mb-2 flex justify-between text-[11px]"><span className="font-mono text-slate-300">{path}</span><span className="font-bold text-white">{count} / 80</span></div>
                    <div className="h-2 overflow-hidden rounded-full bg-slate-800"><div className="h-full rounded-full bg-gradient-to-r from-violet-500 to-blue-400" style={{ width: `${(count / 80) * 100}%` }} /></div>
                  </div>
                ))}
              </div>
              <p className="mt-6 text-xs leading-relaxed text-slate-400">Most events received only broad global and climate offsets. The profile-aware model can vary its correction with each event’s thermal structure, precipitation, elevation, baseline SLR and profile completeness.</p>
            </Panel>
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-3">
            <NarrativeStep number="01" title="Wrong aggregate direction">Training mean log(obs/base) was {signed.format(data.northeast.rootCauseDiagnostic.trainLogObservedOverBaseline.mean)}; the untouched test mean was {signed.format(data.northeast.rootCauseDiagnostic.testLogObservedOverBaseline.mean)}.</NarrativeStep>
            <NarrativeStep number="02" title="Station heterogeneity">Held-out station means ranged from {signed.format(data.northeast.rootCauseDiagnostic.testLogObservedOverBaseline.stationMeanRange[0])} to {signed.format(data.northeast.rootCauseDiagnostic.testLogObservedOverBaseline.stationMeanRange[1])}.</NarrativeStep>
            <NarrativeStep number="03" title="Covariates recover structure">Full-ridge MAE improved {format(data.regions[3].baseline.mae)} → {format(data.regions[3].prediction.mae)} across all 4 stations, 21/27 storms, 4/4 geographic slices and 2/2 elevation bands.</NarrativeStep>
          </div>

          <div className="mt-5 overflow-hidden rounded-2xl border border-white/10 bg-slate-950/55">
            <div className="grid gap-px bg-white/[0.06] md:grid-cols-3">
              {(["intercept", "ridgeThermal", "ridgeFull"] as const).map((method) => {
                const interval = data.northeast.empiricalGroupedCrossfitIntervals80.methods[method];
                return (
                  <div key={method} className="bg-slate-950 p-5">
                    <h3 className="text-sm font-bold text-white">{METHOD_META[method].label}</h3>
                    <p className="mt-1 text-[10px] text-slate-500">Empirical 80% interval heuristic</p>
                    <div className="mt-5 grid grid-cols-2 gap-3 text-center">
                      <div className="rounded-xl bg-white/[0.03] p-3"><p className="text-[9px] uppercase tracking-wider text-slate-500">Station CV</p><p className="mt-1 font-mono text-lg font-black text-cyan-200">{pct(interval.leaveStationOut.empiricalTestCoverage * 100)}</p></div>
                      <div className="rounded-xl bg-white/[0.03] p-3"><p className="text-[9px] uppercase tracking-wider text-slate-500">Storm CV</p><p className="mt-1 font-mono text-lg font-black text-violet-200">{pct(interval.leaveStormOut.empiricalTestCoverage * 100)}</p></div>
                    </div>
                    <p className="mt-3 text-[10px] leading-relaxed text-slate-500">Mean widths {format(interval.leaveStationOut.meanIntervalWidth)} / {format(interval.leaveStormOut.meanIntervalWidth)}.</p>
                  </div>
                );
              })}
            </div>
          </div>
          <Callout tone="warning" icon={AlertTriangle} title="These are interval heuristics, not conformal guarantees">
            {data.northeast.empiricalGroupedCrossfitIntervals80.warning}
          </Callout>
        </section>

        <section className="scroll-mt-32 pt-20">
          <SectionHeading
            eyebrow="Rejected fallback"
            title="A pooled “global” offset is not worldwide"
            description="The leave-one-region-out prior improves all three development holdouts, then makes untouched Northeast worse. That single failure is enough to reject a global fallback."
            icon={CircleSlash2}
          />
          <div className="mt-7"><GlobalPriorChart regions={data.regions} /></div>
          <Callout tone="danger" icon={ShieldAlert} title="Decision: no global fallback">
            Northeast MAE worsens from 4.913 to 5.081 after multiplying Kuchera by 0.923890, a pooled offset fit on Rockies, Sierra and Swiss. Unsupported regions continue to use unchanged physical Kuchera/Cobb behavior.
          </Callout>
        </section>

        <section id="coverage" className="scroll-mt-32 pt-20">
          <SectionHeading
            eyebrow="Coverage and evidence gates"
            title="“No improvement” was usually “no valid labels”"
            description="Only Northeast was a genuine modeling-transfer failure. The other searches stopped earlier because physical-label, authorization, spatial-support or reproducibility gates failed."
            icon={Database}
          />
          <div className="mt-7 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {data.failureTaxonomy.map((item) => (
              <div key={item.name} className="flex flex-col rounded-2xl border border-white/[0.08] bg-white/[0.025] p-5">
                <div className="flex items-start justify-between gap-3">
                  <h3 className="text-sm font-bold text-white">{item.name}</h3>
                  <StatusBadge category={item.category}>{item.status}</StatusBadge>
                </div>
                <p className="mt-4 flex-1 text-xs leading-relaxed text-slate-400">{item.detail}</p>
                {item.evidence && <code className="mt-4 block break-all rounded-lg bg-black/20 px-2.5 py-2 text-[9px] text-slate-600">{item.evidence}</code>}
              </div>
            ))}
          </div>
          <div className="mt-5 grid gap-4 md:grid-cols-4">
            <BigNumber value="239" label="stations audited" />
            <BigNumber value="5,205" label="source-N near misses" />
            <BigNumber value="323" label="exact-10:1 rejects" />
            <BigNumber value="0" label="strict admissible events" accent="rose" />
          </div>
        </section>

        <section id="science" className="scroll-mt-32 pt-20">
          <SectionHeading
            eyebrow="Model and data methodology"
            title="Interpretable upgrades, explicit boundaries"
            description="The diagnostic is intentionally CPU-light and auditable. It tests increasingly expressive residual models without manufacturing predictors or changing the physical baseline."
            icon={Braces}
          />
          <div className="mt-7 grid gap-4 lg:grid-cols-3">
            <MethodologyCard icon={Snowflake} title="Original baseline" kicker="Kuchera physics">Exact MySnow Kuchera SLR remains the comparison anchor and live physical baseline. No fixed-10:1 or Open-Meteo comparison mode is calibrated.</MethodologyCard>
            <MethodologyCard icon={ThermometerSnowflake} title="Profile-aware residual" kicker="32 retained features">Learns log(observed SLR / baseline SLR) from physically present surface, vertical-profile and event fields using regularized ridge regression.</MethodologyCard>
            <MethodologyCard icon={Target} title="Leakage-safe evaluation" kicker="Stations + storms">Shared hyperparameters freeze after training-only grouped CV. Each regional holdout is evaluated once; Northeast stays entirely outside selection.</MethodologyCard>
          </div>

          <div className="mt-5 grid gap-4 xl:grid-cols-[1.1fr_.9fr]">
            <Panel>
              <PanelTitle title="Predictors actually available" description={data.predictors.note} />
              <div className="mt-5 flex flex-wrap gap-2">
                {data.predictors.allAvailableFeatureNames.map((feature) => <code key={feature} className="rounded-lg border border-white/[0.06] bg-white/[0.025] px-2.5 py-1.5 text-[9px] text-slate-400">{feature}</code>)}
              </div>
            </Panel>
            <Panel>
              <PanelTitle title="Balancing candidates" description="All schemes are normalized to sum to the event count." />
              <dl className="mt-5 space-y-3">
                {Object.entries(data.selection.balances).filter(([key]) => key !== "normalization").map(([key, value]) => (
                  <div key={key} className="grid grid-cols-[70px_1fr] gap-3 border-b border-white/[0.05] pb-3 last:border-0"><dt className="font-mono text-[10px] font-bold text-cyan-200">{key}</dt><dd className="text-[11px] leading-relaxed text-slate-400">{value}</dd></div>
                ))}
              </dl>
            </Panel>
          </div>

          <div className="mt-12">
            <SectionHeading eyebrow="Data rights and scientific caveats" title="Why this still is not a production model" description="Strong retrospective metrics do not override provenance, licensing, support and calibration requirements." icon={LockKeyhole} compact />
            <div className="mt-6 grid gap-3 lg:grid-cols-2">
              <Callout tone="danger" icon={ShieldAlert} title="U.S. source-N / CoCoRaHS authorization unresolved">{data.usLabelWarning.warning}</Callout>
              <Callout tone="warning" icon={AlertTriangle} title="Hosted profile-source rights unresolved">Open-Meteo Historical Forecast remains research-only because hosted commercial-use rights have not been cleared.</Callout>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {data.limitations.map((limitation) => <div key={limitation} className="flex gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 text-[11px] leading-relaxed text-slate-400"><Info className="mt-0.5 h-4 w-4 shrink-0 text-slate-600" />{limitation}</div>)}
              <div className="flex gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 text-[11px] leading-relaxed text-slate-400"><Info className="mt-0.5 h-4 w-4 shrink-0 text-slate-600" />Storm folds are complete contiguous blocks, but not strict forward-chaining.</div>
              <div className="flex gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 text-[11px] leading-relaxed text-slate-400"><Info className="mt-0.5 h-4 w-4 shrink-0 text-slate-600" />No humidity or wind predictors are retained even though literature supports both.</div>
            </div>
          </div>

          <div className="mt-16">
            <SectionHeading eyebrow="Verified literature" title="Why these methodological changes are physically motivated" description="References are reproduced from the committed research note. Each card separates the source finding from the implication for MySnow." icon={BookOpen} compact />
            <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {data.citations.map((citation) => (
                <article key={citation.id} className="group flex flex-col rounded-2xl border border-white/[0.08] bg-white/[0.025] p-5 transition hover:border-cyan-300/20 hover:bg-white/[0.04]">
                  <div className="flex items-start justify-between gap-3"><p className="text-[10px] font-black uppercase tracking-[0.15em] text-cyan-300/80">{citation.authors}{citation.year ? ` · ${citation.year}` : ""}</p>{citation.url && <a href={citation.url} target="_blank" rel="noreferrer" aria-label={`Open ${citation.title}`} className="rounded-lg p-1.5 text-slate-600 transition hover:bg-white/5 hover:text-cyan-200 focus:outline-none focus:ring-2 focus:ring-cyan-400/50"><ExternalLink className="h-4 w-4" /></a>}</div>
                  <h3 className="mt-3 text-sm font-bold leading-snug text-white">{citation.title}</h3>
                  {citation.venue && <p className="mt-2 text-[10px] text-slate-500">{citation.venue}</p>}
                  {citation.doi && <code className="mt-2 break-all text-[9px] text-violet-300/70">DOI {citation.doi}</code>}
                  <p className="mt-5 flex-1 border-t border-white/[0.06] pt-4 text-xs leading-relaxed text-slate-400"><strong className="text-slate-300">MySnow implication:</strong> {citation.implication}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <footer className="mt-20 overflow-hidden rounded-[28px] border border-white/10 bg-gradient-to-br from-slate-900/80 to-slate-950 p-6 sm:p-8">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
            <div className="max-w-2xl">
              <div className="flex items-center gap-2 text-cyan-200"><CheckCircle2 className="h-5 w-5" /><span className="text-[10px] font-black uppercase tracking-[0.2em]">Deterministic research snapshot</span></div>
              <h2 className="mt-4 text-2xl font-black text-white">Reproducible, inspectable, not deployed.</h2>
              <p className="mt-3 text-sm leading-relaxed text-slate-400">No v2 artifact was emitted, no runtime registration was added, and both production gates remain off. The original physical algorithm remains live.</p>
            </div>
            <div className="grid gap-2 font-mono text-[9px] text-slate-500 lg:max-w-xl">
              <code className="break-all rounded-lg bg-black/25 p-3">schema: {data.metadata.schemaVersion} · {data.metadata.status}</code>
              <code className="break-all rounded-lg bg-black/25 p-3">diagnostics sha256: {data.metadata.diagnosticsSha256}</code>
              <code className="break-all rounded-lg bg-black/25 p-3">diagnostics commit: {data.metadata.diagnosticsCommit}</code>
              <code className="break-all rounded-lg bg-black/25 p-3">research commit: {data.metadata.researchCommit}</code>
              <code className="break-all rounded-lg bg-black/25 p-3">bootstrap: {data.metadata.bootstrapSeedPolicy}</code>
            </div>
          </div>
          <div className="mt-7 flex flex-col gap-3 border-t border-white/[0.07] pt-6 text-[10px] text-slate-500 sm:flex-row sm:items-center sm:justify-between">
            <span>productionEnablement: <strong className="text-rose-300">false</strong> · artifactProduced: <strong className="text-rose-300">false</strong></span>
            <Link href="/" className="inline-flex items-center gap-2 font-bold text-cyan-200 transition hover:text-white">Back to live forecast <ArrowRight className="h-3.5 w-3.5" /></Link>
          </div>
        </footer>
      </div>
    </main>
  );
}

function ResearchHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-white/[0.07] bg-slate-950/80 backdrop-blur-xl">
      <div className="mx-auto flex h-[61px] max-w-[1440px] items-center justify-between px-4 sm:px-6 lg:px-10">
        <Link href="/" className="group inline-flex items-center gap-3 rounded-lg focus:outline-none focus:ring-2 focus:ring-cyan-400/50">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-blue-500 text-white shadow-lg shadow-blue-500/20"><Snowflake className="h-4 w-4" /></span>
          <span><span className="block text-sm font-black text-white">MySnow</span><span className="block text-[8px] font-bold uppercase tracking-[0.18em] text-slate-500">Model research</span></span>
        </Link>
        <div className="flex items-center gap-2">
          <span className="hidden rounded-full border border-amber-300/20 bg-amber-300/[0.06] px-3 py-1.5 text-[9px] font-black uppercase tracking-[0.15em] text-amber-200 sm:inline-flex">research only</span>
          <Link href="/" className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-[10px] font-bold text-slate-300 transition hover:bg-white/[0.08] hover:text-white focus:outline-none focus:ring-2 focus:ring-cyan-400/50"><ArrowLeft className="h-3.5 w-3.5" />Forecast</Link>
        </div>
      </div>
    </header>
  );
}

function Hero({ data }: { data: SlrResearchViewModel }) {
  return (
    <section className="relative overflow-hidden rounded-[32px] border border-cyan-300/10 bg-gradient-to-br from-slate-900/80 via-slate-950/90 to-blue-950/50 px-5 py-10 shadow-2xl shadow-black/30 sm:px-8 sm:py-14 lg:px-12 lg:py-16">
      <div className="absolute right-0 top-0 h-72 w-72 translate-x-1/3 -translate-y-1/3 rounded-full bg-cyan-400/10 blur-3xl" />
      <div className="relative max-w-4xl">
        <div className="inline-flex items-center gap-2 rounded-full border border-amber-300/20 bg-amber-300/[0.07] px-3 py-1.5 text-[9px] font-black uppercase tracking-[0.18em] text-amber-200"><FlaskConical className="h-3.5 w-3.5" />Research-only diagnostic · not deployed</div>
        <h1 className="mt-6 max-w-4xl text-4xl font-black tracking-[-0.04em] text-white sm:text-5xl lg:text-6xl">Profile-aware SLR vs.<br /><span className="bg-gradient-to-r from-cyan-200 via-blue-300 to-violet-300 bg-clip-text text-transparent">original MySnow Kuchera</span></h1>
        <p className="mt-6 max-w-3xl text-sm leading-7 text-slate-300 sm:text-base">A region-by-region audit of how a regularized atmospheric-profile residual model compares with the exact physical baseline on fixed, unseen-station holdouts—plus the uncertainty, weak slices, failed regions, licensing limits and reasons it remains off.</p>
        <div className="mt-8 flex flex-wrap gap-2">
          <Pill strong>4 held-out regions</Pill><Pill strong>245 test events</Pill><Pill strong>22 unseen stations</Pill><Pill strong>68 complete storms</Pill><Pill>production gates off</Pill><Pill>no v2 artifact</Pill>
        </div>
      </div>
      <div className="relative mt-10 grid gap-3 sm:grid-cols-3 lg:max-w-4xl">
        <HeroFact icon={Sparkles} label="Candidate" value="32-feature ridge" />
        <HeroFact icon={GitBranch} label="Validation" value="Station + storm CV" />
        <HeroFact icon={ShieldAlert} label="Deployment" value={data.metadata.productionEnablement ? "Enabled" : "Disabled"} warning />
      </div>
    </section>
  );
}

function SectionHeading({ eyebrow, title, description, icon: Icon, compact = false }: { eyebrow: string; title: string; description: string; icon: typeof BarChart3; compact?: boolean }) {
  return (
    <div className={`flex flex-col gap-4 sm:flex-row sm:items-start ${compact ? "max-w-5xl" : "max-w-6xl"}`}>
      <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-cyan-300/10 bg-cyan-400/[0.07] text-cyan-200"><Icon className="h-5 w-5" /></div>
      <div><p className="text-[10px] font-black uppercase tracking-[0.22em] text-cyan-300/70">{eyebrow}</p><h2 className={`${compact ? "text-2xl" : "text-2xl sm:text-3xl"} mt-2 font-black tracking-tight text-white`}>{title}</h2><p className="mt-3 max-w-4xl text-sm leading-relaxed text-slate-400">{description}</p></div>
    </div>
  );
}

function RegionOverviewCard({ region, active, onSelect }: { region: RegionView; active: boolean; onSelect: () => void }) {
  return (
    <button onClick={onSelect} className={`group relative overflow-hidden rounded-2xl border p-5 text-left transition focus:outline-none focus:ring-2 focus:ring-cyan-400/60 ${active ? "border-cyan-300/30 bg-cyan-300/[0.06] shadow-lg shadow-cyan-950/20" : "border-white/[0.08] bg-white/[0.025] hover:border-white/15 hover:bg-white/[0.04]"}`}>
      <div className="absolute inset-x-0 top-0 h-px" style={{ background: `linear-gradient(90deg, transparent, ${region.accent}, transparent)` }} />
      <div className="flex items-center justify-between gap-3"><div><p className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-500">{region.holdout.testEvents} events · {region.holdout.testStations} stations</p><h3 className="mt-2 text-base font-bold text-white">{region.shortName}</h3></div><ArrowRight className={`h-4 w-4 transition ${active ? "text-cyan-200" : "text-slate-700 group-hover:text-slate-400"}`} /></div>
      <div className="mt-6 flex items-end gap-2 font-mono"><span className="text-xl font-bold text-slate-500">{format(region.baseline.mae)}</span><span className="pb-1 text-slate-700">→</span><span className="text-2xl font-black text-cyan-200">{format(region.prediction.mae)}</span></div>
      <p className="mt-1 text-[9px] font-bold uppercase tracking-wider text-slate-600">held-out MAE</p>
      <div className="mt-5 flex items-center justify-between border-t border-white/[0.06] pt-4"><span className="text-xs font-bold text-emerald-300">−{pct(region.maeImprovementPct)}</span><span className={`text-[9px] font-semibold ${region.biasMagnitudeImproved ? "text-slate-500" : "text-amber-300"}`}>{region.biasMagnitudeImproved ? "bias magnitude improved" : "bias magnitude worsened"}</span></div>
    </button>
  );
}

function RegionTabs({ regions, selected, onSelect }: { regions: RegionView[]; selected: RegionId; onSelect: (id: RegionId) => void }) {
  return (
    <div role="tablist" aria-label="Select region" className="no-scrollbar mt-7 flex gap-2 overflow-x-auto rounded-2xl border border-white/[0.08] bg-white/[0.02] p-2">
      {regions.map((region) => <button key={region.id} role="tab" aria-selected={selected === region.id} onClick={() => onSelect(region.id)} className={`min-w-fit flex-1 rounded-xl px-4 py-3 text-xs font-bold transition focus:outline-none focus:ring-2 focus:ring-cyan-400/60 ${selected === region.id ? "bg-white/10 text-white shadow-lg" : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-300"}`}>{region.shortName}</button>)}
    </div>
  );
}

function MetricComparison({ label, baseline, prediction, change, signedMetric = false, warning = false }: { label: string; baseline: number; prediction: number; change: number; signedMetric?: boolean; warning?: boolean }) {
  return (
    <div className={`rounded-2xl border p-4 ${warning ? "border-amber-300/20 bg-amber-300/[0.05]" : "border-white/[0.08] bg-slate-950/40"}`}>
      <p className="text-[9px] font-black uppercase tracking-[0.15em] text-slate-500">{label}</p>
      <div className="mt-3 flex items-baseline gap-2 font-mono"><span className="text-base font-bold text-slate-500">{signedMetric ? signed.format(baseline) : format(baseline)}</span><span className="text-slate-700">→</span><span className={`text-2xl font-black ${warning ? "text-amber-200" : "text-cyan-200"}`}>{signedMetric ? signed.format(prediction) : format(prediction)}</span></div>
      <p className={`mt-2 text-[10px] font-semibold ${warning ? "text-amber-300" : "text-emerald-300"}`}>{signedMetric ? `${change >= 0 ? "improves" : "worsens"} |bias| by ${format(Math.abs(change))}` : `${pct(change)} lower`}</p>
    </div>
  );
}

function SliceTable({ dimension, defaultOpen }: { dimension: RegionView["slices"][number]; defaultOpen: boolean }) {
  const [sort, setSort] = useState<"label" | "events" | "improvement">("improvement");
  const rows = useMemo(() => [...dimension.rows].sort((a, b) => sort === "events" ? b.eventCount - a.eventCount : sort === "improvement" ? b.maeImprovementPct - a.maeImprovementPct : a.label.localeCompare(b.label)), [dimension.rows, sort]);
  return (
    <details open={defaultOpen} className="group overflow-hidden rounded-2xl border border-white/[0.08] bg-slate-950/45">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-4 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-cyan-400/50 sm:px-5"><div><span className="text-sm font-bold text-white">{dimension.label}</span><span className="ml-2 text-[10px] text-slate-500">{dimension.total} slices · {dimension.improved} improve</span></div><ChevronDown className="h-4 w-4 text-slate-500 transition group-open:rotate-180" /></summary>
      <div className="border-t border-white/[0.06]">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-5"><p className="text-[10px] text-slate-500">Sorted tabular alternative to the aggregate chart.</p><label className="flex items-center gap-2 text-[10px] text-slate-500">Sort<select value={sort} onChange={(event) => setSort(event.target.value as typeof sort)} className="rounded-lg border border-white/10 bg-slate-900 px-2 py-1.5 text-slate-300 focus:outline-none focus:ring-2 focus:ring-cyan-400/50"><option value="improvement">MAE improvement</option><option value="events">Event count</option><option value="label">Name</option></select></label></div>
        <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-[11px]"><thead className="bg-white/[0.025] text-[8px] uppercase tracking-[0.12em] text-slate-600"><tr><th className="px-4 py-3 sm:px-5">Slice</th><th className="px-3 py-3 text-right">Events</th><th className="px-3 py-3 text-right">Kuchera MAE</th><th className="px-3 py-3 text-right">Full-ridge MAE</th><th className="px-3 py-3 text-right">Change</th><th className="px-3 py-3 text-right">Kuchera bias</th><th className="px-4 py-3 text-right sm:px-5">Full-ridge bias</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id} className="border-t border-white/[0.04]"><td className="px-4 py-3 font-medium text-slate-300 sm:px-5">{row.label}</td><td className="px-3 py-3 text-right font-mono text-slate-500">{row.eventCount}</td><td className="px-3 py-3 text-right font-mono text-slate-400">{format(row.metrics.baseline.mae)}</td><td className="px-3 py-3 text-right font-mono font-bold text-cyan-200">{format(row.metrics.ridgeFull.mae)}</td><td className={`px-3 py-3 text-right font-mono font-bold ${row.maeImprovement > 0 ? "text-emerald-300" : "text-rose-300"}`}>{row.maeImprovement > 0 ? "−" : "+"}{pct(Math.abs(row.maeImprovementPct))}</td><td className="px-3 py-3 text-right font-mono text-slate-500">{signed.format(row.metrics.baseline.bias)}</td><td className="px-4 py-3 text-right font-mono text-slate-400 sm:px-5">{signed.format(row.metrics.ridgeFull.bias)}</td></tr>)}</tbody></table></div>
      </div>
    </details>
  );
}

function Panel({ children, className = "" }: { children: React.ReactNode; className?: string }) { return <div className={`rounded-2xl border border-white/[0.08] bg-white/[0.025] p-5 sm:p-6 ${className}`}>{children}</div>; }
function PanelTitle({ title, description }: { title: string; description: string }) { return <div><h3 className="text-sm font-bold text-white">{title}</h3><p className="mt-1 text-[11px] leading-relaxed text-slate-500">{description}</p></div>; }
function Stat({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) { return <div><dt className="text-[9px] font-bold uppercase tracking-wider text-slate-600">{label}</dt><dd className={`mt-1 font-mono text-lg font-black ${accent ? "text-cyan-200" : "text-white"}`}>{value}</dd></div>; }
function MiniStat({ label, value }: { label: string; value: number }) { return <div className="rounded-xl border border-white/[0.06] bg-black/15 p-3 text-center"><div className="font-mono text-xl font-black text-white">{value}</div><div className="mt-1 text-[8px] font-bold uppercase tracking-wider text-slate-600">{label}</div></div>; }
function Pill({ children, strong = false }: { children: React.ReactNode; strong?: boolean }) { return <span className={`rounded-full border px-3 py-1.5 text-[9px] font-bold ${strong ? "border-cyan-300/15 bg-cyan-300/[0.06] text-cyan-100" : "border-white/[0.08] bg-white/[0.025] text-slate-400"}`}>{children}</span>; }
function HeroFact({ icon: Icon, label, value, warning = false }: { icon: typeof Sparkles; label: string; value: string; warning?: boolean }) { return <div className="flex items-center gap-3 rounded-2xl border border-white/[0.08] bg-black/15 p-4"><Icon className={`h-5 w-5 ${warning ? "text-amber-300" : "text-cyan-300"}`} /><div><p className="text-[8px] font-black uppercase tracking-[0.15em] text-slate-600">{label}</p><p className="mt-1 text-xs font-bold text-white">{value}</p></div></div>; }
function NarrativeStep({ number, title, children }: { number: string; title: string; children: React.ReactNode }) { return <div className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-5"><span className="font-mono text-[10px] font-black text-cyan-300/60">{number}</span><h3 className="mt-3 text-sm font-bold text-white">{title}</h3><p className="mt-2 text-xs leading-relaxed text-slate-400">{children}</p></div>; }
function BigNumber({ value, label, accent = "cyan" }: { value: string; label: string; accent?: "cyan" | "rose" }) { return <div className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-5 text-center"><div className={`font-mono text-3xl font-black ${accent === "rose" ? "text-rose-300" : "text-cyan-200"}`}>{value}</div><div className="mt-2 text-[9px] font-bold uppercase tracking-[0.13em] text-slate-600">{label}</div></div>; }
function MethodologyCard({ icon: Icon, title, kicker, children }: { icon: typeof Snowflake; title: string; kicker: string; children: React.ReactNode }) { return <div className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-5"><div className="flex items-center gap-3"><span className="grid h-9 w-9 place-items-center rounded-xl bg-cyan-400/[0.07] text-cyan-200"><Icon className="h-4 w-4" /></span><div><p className="text-[8px] font-black uppercase tracking-[0.15em] text-slate-600">{kicker}</p><h3 className="mt-1 text-sm font-bold text-white">{title}</h3></div></div><p className="mt-4 text-xs leading-relaxed text-slate-400">{children}</p></div>; }
function StatusBadge({ category, children }: { category: "data" | "spatial" | "model" | "reproducibility"; children: React.ReactNode }) { const styles = { data: "bg-rose-400/10 text-rose-200", spatial: "bg-amber-300/10 text-amber-200", model: "bg-cyan-400/10 text-cyan-200", reproducibility: "bg-violet-400/10 text-violet-200" }; return <span className={`max-w-[145px] rounded-full px-2.5 py-1 text-right text-[8px] font-black uppercase leading-tight tracking-wider ${styles[category]}`}>{children}</span>; }
function Callout({ tone, icon: Icon, title, children }: { tone: "neutral" | "warning" | "danger"; icon: typeof Info; title: string; children: React.ReactNode }) { const styles = { neutral: "border-blue-300/15 bg-blue-300/[0.04] text-blue-100", warning: "border-amber-300/20 bg-amber-300/[0.05] text-amber-100", danger: "border-rose-300/20 bg-rose-300/[0.05] text-rose-100" }; return <div className={`mt-5 flex gap-3 rounded-2xl border p-4 sm:p-5 ${styles[tone]}`}><Icon className="mt-0.5 h-5 w-5 shrink-0 opacity-80" /><div><h3 className="text-xs font-bold">{title}</h3><p className="mt-1 text-[11px] leading-relaxed opacity-70">{children}</p></div></div>; }
