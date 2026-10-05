#!/usr/bin/env python3
"""Generate graphs and a KPI table from a recorded simulation run.

    scripts/plot_results.py runtime/results/demo_run [--out project_report/figures/demo]

Reads tasks.csv, agv_trace.csv, occupancy.csv, alerts.csv, metrics.json written by
uco_wms/metrics_recorder and writes PNG figures plus kpi_table.md into --out (default: <run>/figures).
"""
import argparse
import csv
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'ros2_ws', 'src', 'uco_common'))

import matplotlib  # noqa: E402
matplotlib.use('Agg')
import matplotlib.patches as mp  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from uco_common.layout import ZONE_RESTRICTED, load_layout  # noqa: E402

STATE_COLOR = {'IDLE': '#cbd5e1', 'NAVIGATING': '#3b82f6', 'PICKING': '#f59e0b', 'DROPPING': '#22c55e',
               'CHARGING': '#a855f7', 'GOING_TO_CHARGE': '#c084fc', 'PAUSED': '#ef4444', 'ERROR': '#7f1d1d'}


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def fnum(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def plot_trajectory(L, trace, out):
    fig, ax = plt.subplots(figsize=(11, 7.5))
    W, H = L.size
    ax.add_patch(mp.Rectangle((0, 0), W, H, fill=False, lw=3, ec='#444'))
    for z in L.zones:
        r = z.rect
        ax.add_patch(mp.Rectangle((r.xmin, r.ymin), r.size[0], r.size[1], fc=(*z.color, 0.10), ec=z.color, lw=1,
                                  hatch='//' if z.kind == ZONE_RESTRICTED else None))
    for o in L.obstacles:
        if o.shape == 'box':
            ax.add_patch(mp.Rectangle((o.center[0] - o.size[0] / 2, o.center[1] - o.size[1] / 2), o.size[0], o.size[1],
                                      fc=o.color, alpha=0.8 if o.collision else 0.3))
        else:
            ax.add_patch(mp.Circle(o.center, o.radius, fc=o.color))
    for loc in L.slots():
        ax.add_patch(mp.Rectangle((loc.slot.x - 0.6, loc.slot.y - 0.6), 1.2, 1.2, fill=False, ec='#94a3b8', lw=0.6))
    xs = [fnum(r['x']) for r in trace]
    ys = [fnum(r['y']) for r in trace]
    loaded = [bool(r['carrying']) for r in trace]
    for i in range(1, len(xs)):
        if None in (xs[i], ys[i], xs[i - 1], ys[i - 1]) or (xs[i] == 0 and ys[i] == 0):
            continue
        ax.plot(xs[i - 1:i + 1], ys[i - 1:i + 1], color='#dc2626' if loaded[i] else '#2563eb', lw=1.4, alpha=0.8)
    ax.plot([], [], color='#2563eb', label='AGV empty')
    ax.plot([], [], color='#dc2626', label='AGV carrying a container')
    ax.legend(loc='upper center', ncol=2)
    ax.set_xlim(-1, W + 1)
    ax.set_ylim(-1, H + 1)
    ax.set_aspect('equal')
    ax.set_title('AGV-01 trajectory (AMCL pose, 1 Hz)')
    ax.set_xlabel('x [m]')
    ax.set_ylabel('y [m]')
    fig.savefig(os.path.join(out, 'agv_trajectory.png'), dpi=110, bbox_inches='tight')
    plt.close(fig)


def plot_battery_and_state(trace, out):
    t0 = fnum(trace[0]['t_s'], 0.0)
    t = [fnum(r['t_s'], t0) - t0 for r in trace]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 5.5), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
    a1.plot(t, [fnum(r['battery_pct']) for r in trace], color='#16a34a', lw=1.8, label='battery %')
    a1.axhline(25, color='#dc2626', ls='--', lw=1, label='low threshold 25 %')
    a1.axhline(80, color='#7c3aed', ls=':', lw=1, label='resume threshold 80 %')
    a1.set_ylabel('state of charge [%]')
    a1.set_ylim(0, 105)
    a1.legend(loc='lower left')
    ax_v = a1.twinx()
    ax_v.plot(t, [abs(fnum(r['speed'], 0)) for r in trace], color='#2563eb', lw=0.6, alpha=0.5)
    ax_v.set_ylabel('speed [m/s]', color='#2563eb')
    start = 0
    for i in range(1, len(trace) + 1):
        if i == len(trace) or trace[i]['state'] != trace[start]['state']:
            a2.axvspan(t[start], t[i - 1] + 1, color=STATE_COLOR.get(trace[start]['state'], '#999'), lw=0)
            start = i
    a2.set_yticks([])
    a2.set_xlabel('simulation time [s]')
    a2.legend(handles=[mp.Patch(color=c, label=s) for s, c in STATE_COLOR.items()
                       if any(r['state'] == s for r in trace)], loc='upper center', bbox_to_anchor=(0.5, -0.55),
              ncol=7, fontsize=8)
    a1.set_title('AGV-01 battery, speed and operating state')
    fig.savefig(os.path.join(out, 'agv_battery_state.png'), dpi=110, bbox_inches='tight')
    plt.close(fig)


def plot_localization(trace, out):
    rows = [r for r in trace if r.get('loc_error_m') not in (None, '')]
    if not rows:
        return
    t0 = fnum(trace[0]['t_s'], 0.0)
    fig, a1 = plt.subplots(figsize=(11, 3.6))
    a1.plot([fnum(r['t_s']) - t0 for r in rows], [fnum(r['loc_error_m']) for r in rows], color='#dc2626', lw=1.2,
            label='AMCL position error vs ground truth')
    a1.set_ylabel('error [m]', color='#dc2626')
    a1.set_xlabel('simulation time [s]')
    a1.grid(alpha=0.3)
    sc = [r for r in rows if r.get('loc_score') not in (None, '')]
    if sc:
        a2 = a1.twinx()
        a2.plot([fnum(r['t_s']) - t0 for r in sc], [fnum(r['loc_score']) for r in sc], color='#2563eb', lw=0.8,
                alpha=0.7, label='scan / map match score')
        a2.axhline(0.4, color='#f59e0b', ls='--', lw=1, label='degraded < 0.4')
        a2.axhline(0.2, color='#7f1d1d', ls='--', lw=1, label='lost < 0.2')
        a2.set_ylim(0, 1.05)
        a2.set_ylabel('match score', color='#2563eb')
        a2.legend(loc='lower right', fontsize=8)
    a1.legend(loc='upper left', fontsize=8)
    a1.set_title('Localisation: AMCL error and scan-to-map match score')
    fig.savefig(os.path.join(out, 'localization.png'), dpi=110, bbox_inches='tight')
    plt.close(fig)


def plot_occupancy(occ, out):
    t0 = fnum(occ[0]['t_s'], 0.0)
    t = [fnum(r['t_s'], t0) - t0 for r in occ]
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(t, [int(r['storage_occupied']) for r in occ], label='storage positions occupied', color='#2563eb', lw=2)
    ax.plot(t, [int(r['holding']) for r in occ], label='holding area', color='#f59e0b', lw=1.5)
    ax.plot(t, [int(r['dispatch_buffer']) for r in occ], label='dispatch buffer', color='#16a34a', lw=1.5)
    ax.plot(t, [int(r['received_total']) for r in occ], label='received (cumulative)', color='#64748b', ls='--')
    ax.plot(t, [int(r['dispatched_total']) for r in occ], label='dispatched (cumulative)', color='#a855f7', ls='--')
    ax.set_xlabel('simulation time [s]')
    ax.set_ylabel('containers')
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(alpha=0.3)
    ax.set_title('Warehouse occupancy and container flow')
    fig.savefig(os.path.join(out, 'occupancy.png'), dpi=110, bbox_inches='tight')
    plt.close(fig)


def plot_tasks(tasks, out):
    done = [r for r in tasks if r['status'] == 'COMPLETED']
    if not done:
        return
    fig, ax = plt.subplots(figsize=(11, 4))
    labels = [f"{r['task_id']}\n{r['type']}" for r in done]
    wait = [fnum(r['waiting_s'], 0) for r in done]
    exe = [fnum(r['execution_s'], 0) for r in done]
    ax.bar(labels, wait, color='#f59e0b', label='waiting (created → started)')
    ax.bar(labels, exe, bottom=wait, color='#2563eb', label='execution (started → completed)')
    for i, r in enumerate(done):
        if int(r['attempts'] or 0):
            ax.text(i, wait[i] + exe[i] + 3, f"retry ×{r['attempts']}", ha='center', fontsize=8, color='#dc2626')
    ax.set_ylabel('time [s]')
    ax.legend()
    ax.set_title('Transport task durations')
    ax.grid(axis='y', alpha=0.3)
    fig.savefig(os.path.join(out, 'task_durations.png'), dpi=110, bbox_inches='tight')
    plt.close(fig)


def plot_alerts(alerts, trace, out):
    if not alerts:
        return
    t0 = fnum(trace[0]['t_s'], 0.0) if trace else fnum(alerts[0]['t_s'], 0.0)
    sev_y = {'INFO': 0, 'WARN': 1, 'ERROR': 2, 'ALARM': 3}
    col = {'INFO': '#64748b', 'WARN': '#f59e0b', 'ERROR': '#dc2626', 'ALARM': '#7f1d1d'}
    fig, ax = plt.subplots(figsize=(11, 3.4))
    for a in alerts:
        t = fnum(a['t_s'], t0) - t0
        ax.scatter(t, sev_y.get(a['severity'], 0), color=col.get(a['severity']), s=18)
    shown = set()
    for a in alerts:
        if a['severity'] in ('ERROR', 'ALARM') and a['code'] not in shown:
            shown.add(a['code'])
            ax.annotate(a['code'], (fnum(a['t_s'], t0) - t0, sev_y[a['severity']]), fontsize=7, rotation=30,
                        xytext=(2, 6), textcoords='offset points')
    ax.set_yticks(list(sev_y.values()), list(sev_y))
    ax.set_ylim(-0.5, 4)
    ax.set_xlabel('simulation time [s]')
    ax.set_title('Alerts and alarms')
    ax.grid(alpha=0.3)
    fig.savefig(os.path.join(out, 'alerts_timeline.png'), dpi=110, bbox_inches='tight')
    plt.close(fig)


def kpi_table(metrics, out):
    m = metrics
    rows = [
        ('Simulated duration', f"{m['sim_duration_s']:.0f} s"),
        ('Containers received / stored now / dispatched',
         f"{m['containers']['received']} / {m['containers']['stored_now']} / {m['containers']['dispatched']}"),
        ('Tasks completed / failed / cancelled',
         f"{m['tasks']['completed']} / {m['tasks']['failed']} / {m['tasks']['cancelled']}"),
        ('Task completion rate', f"{m['tasks']['completion_rate']:.0%}" if m['tasks']['completion_rate'] is not None
         else '-'),
        ('Retried attempts (recovered failures)', m['tasks']['retried_attempts']),
        ('Average task time (total / waiting / execution)',
         f"{m['tasks']['avg_total_time_s']} / {m['tasks']['avg_waiting_time_s']} / {m['tasks']['avg_execution_time_s']} s"),
        ('Average task time by type', ', '.join(f'{k} {v} s' for k, v in m['tasks']['avg_total_time_by_type_s'].items())),
        ('Throughput', f"{m['throughput']['tasks_per_hour']} tasks/h, {m['throughput']['containers_stored_per_hour']} "
                       f"stored/h, {m['throughput']['containers_retrieved_per_hour']} retrieved/h"),
        ('Storage occupancy (end / max)', f"{m['warehouse']['occupancy_now']:.1%} / {m['warehouse']['occupancy_max']:.1%}"
                                          f" of {m['warehouse']['storage_capacity']} positions"),
        ('AGV travel distance', f"{m['agv']['distance_m']} m"),
        ('Localisation error (mean / p95 / max)',
         f"{m['localization'].get('mean_error_m', '-')} / {m['localization'].get('p95_error_m', '-')} / "
         f"{m['localization'].get('max_error_m', '-')} m" if 'localization' in m else '-'),
        ('AGV utilisation (busy / uptime)', f"{m['agv']['utilization']:.1%}" if m['agv']['utilization'] else '-'),
        ('Battery start → end', f"{m['agv']['battery_start_pct']} % → {m['agv']['battery_end_pct']} %"),
        ('Energy consumed', f"{m['agv']['energy_consumed_wh']} Wh at battery time scale ×{m['agv']['battery_time_scale']}"
                            f" (≈ {m['agv']['energy_consumed_wh_real_time_equivalent']} Wh real-time equivalent)"),
    ]
    text = '| KPI | Value |\n|---|---|\n' + '\n'.join(f'| {k} | {v} |' for k, v in rows) + '\n'
    with open(os.path.join(out, 'kpi_table.md'), 'w', encoding='utf-8') as f:
        f.write(text)
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run_dir')
    ap.add_argument('--out', default='')
    ap.add_argument('--layout', default=os.path.join(ROOT, 'ros2_ws/src/uco_common/config/warehouse_layout.yaml'))
    a = ap.parse_args()
    out = a.out or os.path.join(a.run_dir, 'figures')
    os.makedirs(out, exist_ok=True)
    L = load_layout(a.layout)
    trace = [r for r in read_csv(os.path.join(a.run_dir, 'agv_trace.csv'))
             if fnum(r['battery_pct'], -1) >= 0 and fnum(r['x']) is not None and fnum(r['x']) == fnum(r['x'])]
    occ = read_csv(os.path.join(a.run_dir, 'occupancy.csv'))
    tasks = read_csv(os.path.join(a.run_dir, 'tasks.csv'))
    alerts = read_csv(os.path.join(a.run_dir, 'alerts.csv'))
    if trace:
        plot_trajectory(L, trace, out)
        plot_battery_and_state(trace, out)
        plot_localization(trace, out)
    if occ:
        plot_occupancy(occ, out)
    plot_tasks(tasks, out)
    plot_alerts(alerts, trace, out)
    mpath = os.path.join(a.run_dir, 'metrics.json')
    if os.path.exists(mpath):
        with open(mpath, encoding='utf-8') as f:
            print(kpi_table(json.load(f), out))
    print('figures written to', out)


if __name__ == '__main__':
    main()
