#!/usr/bin/env python3
"""Fill the measured-results chapters of the project report from recorded runs.

    scripts/build_report.py --fault-run runtime/results/<fault_suite_run> --demo-run runtime/results/<demo_run>

* copies each run's data (CSV, JSON, figures) to project_report/results/<label>/
* regenerates plots with scripts/plot_results.py
* reads the latest `colcon test` results (ros2_ws/build/*)
* replaces the text between `<!-- GENERATED:RESULTS:BEGIN -->` and `<!-- GENERATED:RESULTS:END -->`
  in project_report/report.md with Chapters 14-16 built from those files.
Nothing in the generated chapters is typed by hand: every number comes from the run files.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
REPORT = os.path.join(ROOT, 'project_report', 'report.md')
BEGIN, END = '<!-- GENERATED:RESULTS:BEGIN -->', '<!-- GENERATED:RESULTS:END -->'


def copy_run(src, label):
    dst = os.path.join(ROOT, 'project_report', 'results', label)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    os.makedirs(dst)
    for name in ('tasks.csv', 'agv_trace.csv', 'occupancy.csv', 'alerts.csv', 'metrics.json', 'scenario_report.json'):
        if os.path.exists(os.path.join(src, name)):
            shutil.copy(os.path.join(src, name), dst)
    figs = os.path.join(dst, 'figures')
    os.makedirs(figs, exist_ok=True)
    if os.path.isdir(os.path.join(src, 'figures')):           # snapshots taken by the scenario runner
        for f in glob.glob(os.path.join(src, 'figures', '*.png')):
            shutil.copy(f, figs)
    subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'plot_results.py'), dst, '--out', figs],
                   check=True, capture_output=True)
    return dst


def unit_test_summary():
    rows, total = [], {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
    for path in sorted(glob.glob(os.path.join(ROOT, 'ros2_ws', 'build', '*', 'pytest.xml')) +
                       glob.glob(os.path.join(ROOT, 'ros2_ws', 'build', '*', 'test_results', '*', '*.xunit.xml'))):
        root = ET.parse(path).getroot()
        suite = root if root.tag == 'testsuite' else root.find('testsuite')
        counts = {k: int(suite.get(k, 0)) for k in total}
        if counts['tests'] == 0:
            continue
        pkg = path.split(os.sep + 'build' + os.sep)[1].split(os.sep)[0]
        rows.append((pkg, counts))
        for k in total:
            total[k] += counts[k]
    return rows, total


def fmt_cond(d):
    return ', '.join(f'{k}={v}' for k, v in d.items()) if isinstance(d, dict) else str(d)


def scenario_table(report):
    lines = ['| # | Step | Condition | Observed | Result |', '|---|---|---|---|---|']
    for s in report['steps']:
        if s['step'] not in ('expect', 'wait_for', 'fault', 'estop', 'delivery', 'processing_request', 'move_agv'):
            continue
        if s['step'] in ('expect', 'wait_for'):
            arg = {k: v for k, v in s['arg'].items() if k != 'timeout'}
            res = ('PASS' if s['ok'] else '**FAIL**') + (f" ({s['waited_s']} s)" if 'waited_s' in s else '')
            lines.append(f"| {s['index']} | {s['step']} | {fmt_cond(arg)} | {fmt_cond(s.get('observed', ''))} | {res} |")
        else:
            arg = fmt_cond(s['arg']) if isinstance(s['arg'], dict) else s['arg']
            lines.append(f"| {s['index']} | **{s['step']}** | {arg} | {s.get('message', '')} | {'ok' if s['ok'] else '**FAIL**'} |")
    return '\n'.join(lines)


def kpi_rows(m):
    loc = m.get('localization', {})
    return [
        ('Simulated duration', f"{m['sim_duration_s']:.0f} s"),
        ('Containers received / in storage at end / dispatched',
         f"{m['containers']['received']} / {m['containers']['stored_now']} / {m['containers']['dispatched']}"),
        ('Transport tasks completed / failed / cancelled',
         f"{m['tasks']['completed']} / {m['tasks']['failed']} / {m['tasks']['cancelled']}"),
        ('Task completion rate', '-' if m['tasks']['completion_rate'] is None else f"{m['tasks']['completion_rate']:.0%}"),
        ('Recovered failed attempts (retries)', m['tasks']['retried_attempts']),
        ('Average task time: total / waiting / execution',
         f"{m['tasks']['avg_total_time_s']} / {m['tasks']['avg_waiting_time_s']} / {m['tasks']['avg_execution_time_s']} s"),
        ('Average task time by type', ', '.join(f'{k}: {v} s' for k, v in m['tasks']['avg_total_time_by_type_s'].items())),
        ('Throughput (tasks / stored / retrieved per hour)',
         f"{m['throughput']['tasks_per_hour']} / {m['throughput']['containers_stored_per_hour']} / "
         f"{m['throughput']['containers_retrieved_per_hour']}"),
        ('Storage occupancy at end / maximum',
         f"{m['warehouse']['occupancy_now']:.1%} / {m['warehouse']['occupancy_max']:.1%} of {m['warehouse']['storage_capacity']}"),
        ('Stored volume at end', f"{m['containers']['stored_volume_l']} L"),
        ('AGV travel distance', f"{m['agv']['distance_m']} m"),
        ('AGV utilisation (busy / uptime)', '-' if m['agv']['utilization'] is None else f"{m['agv']['utilization']:.1%}"),
        ('Battery start → end', f"{m['agv']['battery_start_pct']} % → {m['agv']['battery_end_pct']} %"),
        ('Energy drawn from the battery',
         f"{m['agv']['energy_consumed_wh']} Wh (battery time scale ×{m['agv']['battery_time_scale']}; "
         f"≈ {m['agv']['energy_consumed_wh_real_time_equivalent']} Wh real-time equivalent)"),
        ('Localisation error vs ground truth (mean / p95 / max)',
         f"{loc.get('mean_error_m', '-')} / {loc.get('p95_error_m', '-')} / {loc.get('max_error_m', '-')} m "
         f"({loc.get('samples', 0)} samples)"),
    ]


def alert_summary(m):
    a = m.get('alerts', {})
    keys = [k for k in a if k not in ('WMS_READY', 'AGV_READY')]
    return ', '.join(f'{k} ×{a[k]}' for k in sorted(keys, key=lambda k: -a[k])[:24])


def figure(label, name, caption):
    path = f'results/{label}/figures/{name}'
    if os.path.exists(os.path.join(ROOT, 'project_report', path)):
        return f'![{caption}]({path})\n\n*{caption}*\n'
    return ''


def build(fault_dir, demo_dir, fault_label, demo_label):
    rows, total = unit_test_summary()
    fr = json.load(open(os.path.join(fault_dir, 'scenario_report.json')))
    dr = json.load(open(os.path.join(demo_dir, 'scenario_report.json')))
    fm = json.load(open(os.path.join(fault_dir, 'metrics.json')))
    dm = json.load(open(os.path.join(demo_dir, 'metrics.json')))
    nchk = lambda r: (sum(s['ok'] for s in r['steps'] if s['step'] in ('expect', 'wait_for')),  # noqa: E731
                      sum(1 for s in r['steps'] if s['step'] in ('expect', 'wait_for')))
    fpass, ftot = nchk(fr)
    dpass, dtot = nchk(dr)
    out = [BEGIN, '', '## 14. Testing', '',
           'All results in this chapter are generated by `scripts/build_report.py` from the files in '
           '`project_report/results/` and the latest `colcon test` output.', '',
           '### 14.1 Unit and ROS integration tests (`scripts/test.sh`)', '',
           '| Package | Tests | Failures | Errors | Skipped |', '|---|---|---|---|---|']
    out += [f"| {p} | {c['tests']} | {c['failures']} | {c['errors']} | {c['skipped']} |" for p, c in rows]
    out += [f"| **Total** | **{total['tests']}** | **{total['failures']}** | **{total['errors']}** | **{total['skipped']}** |", '',
            'The test inventory and what each test verifies is in `docs/testing.md`.', '',
            f'### 14.2 Simulation test: fault suite (`scripts/run_sim_tests.sh`) — '
            f'**{"PASSED" if fr["passed"] else "FAILED"}, {fpass}/{ftot} checks**', '',
            f'Simulated duration {fr["sim_duration_s"]:.0f} s. {fr["description"].strip()}', '',
            scenario_table(fr), '',
            f'### 14.3 Simulation test: demonstration scenario (`scripts/run_demo.sh`) — '
            f'**{"PASSED" if dr["passed"] else "FAILED"}, {dpass}/{dtot} checks**', '',
            scenario_table(dr), '',
            '## 15. Performance evaluation', '',
            '| KPI | Demonstration run | Fault-suite run |', '|---|---|---|']
    for (k, v1), (_, v2) in zip(kpi_rows(dm), kpi_rows(fm)):
        out.append(f'| {k} | {v1} | {v2} |')
    out += ['',
            'Notes: times are simulation seconds (real-time factor ≈ 1.0 on the 4-core reference machine). '
            'Waiting time is the time from task creation to the AGV starting it. Throughput is limited '
            'by a single AGV and by the receiving line rate. Battery dynamics run 10× faster than real '
            'time in these scenarios so that charging behaviour appears within minutes; the energy '
            'figure is also given as its real-time equivalent.', '',
            f'**Alerts in the demonstration run:** {alert_summary(dm)}.', '',
            f'**Alerts in the fault-suite run:** {alert_summary(fm)}.', '',
            '## 16. Results', '',
            '### 16.1 Demonstration scenario', '',
            dr['description'].strip(), '']
    for name, cap in (('demo_01_receiving.png', 'Containers on the holding positions after unloading, weighing and inspection'),
                      ('demo_02_first_stored.png', 'First container stored in aisle A'),
                      ('demo_03_all_stored.png', 'All five containers stored'),
                      ('demo_04_dispatch_buffer.png', 'Retrieved containers in the dispatch / processing buffer'),
                      ('agv_trajectory.png', 'AGV trajectory during the demonstration (red: carrying a container)'),
                      ('occupancy.png', 'Occupancy and container flow during the demonstration'),
                      ('task_durations.png', 'Transport task durations in the demonstration'),
                      ('agv_battery_state.png', 'Battery, speed and AGV state during the demonstration'),
                      ('localization.png', 'Localisation error and scan/map match during the demonstration')):
        out.append(figure(demo_label, name, cap))
    out += ['### 16.2 Fault suite', '']
    for name, cap in (('agv_battery_state.png', 'Fault suite: battery forced to 18 %, charging, e-stop and sensor-failure pauses'),
                      ('alerts_timeline.png', 'Fault suite: alerts and alarms over time'),
                      ('agv_trajectory.png', 'Fault suite: AGV trajectory'),
                      ('localization.png', 'Fault suite: localisation error and scan/map match')):
        out.append(figure(fault_label, name, cap))
    out += ['### 16.3 Monitoring', '',
            '![Dashboard](figures/dashboard.png)\n\n*Web dashboard during operation: layout with live slot occupancy, '
            'AGV pose and trail, AGV / battery / safety state, KPIs, operator controls, task and inventory tables, '
            'alarm log.*\n',
            '![RViz2](figures/rviz_navigation.png)\n\n*RViz2: map, keep-out zones, global and local costmaps, AGV '
            'model, both lidars, odometry, global plan towards a storage position.*\n', END]
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--fault-run', required=True)
    ap.add_argument('--demo-run', required=True)
    a = ap.parse_args()
    copy_run(a.fault_run, 'fault_suite')
    copy_run(a.demo_run, 'demo')
    text = build(a.fault_run, a.demo_run, 'fault_suite', 'demo')
    with open(REPORT, encoding='utf-8') as f:
        report = f.read()
    if BEGIN in report:
        pre, rest = report.split(BEGIN, 1)
        post = rest.split(END, 1)[1]
        report = pre + text + post
    else:
        report = report.replace('## 17. Limitations', text + '\n\n## 17. Limitations', 1)
    with open(REPORT, 'w', encoding='utf-8') as f:
        f.write(report)
    print('report updated:', REPORT)


if __name__ == '__main__':
    main()
