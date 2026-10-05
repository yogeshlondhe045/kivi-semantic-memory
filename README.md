# UCO Warehouse Digital Twin

Automated warehouse simulation for a **used-cooking-oil (UCO) collection facility** that
feeds a biodiesel plant: receiving, weighing, inspection, automated storage, AGV transport,
safety, fault injection, monitoring and dispatch. Built with **ROS 2 Jazzy**, **Gazebo
Harmonic**, **Nav2** and a SQLite-backed warehouse management system.

> Status: under phased development. See [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) for
> which phases are done and how each was verified. The biodiesel chemistry itself is out of scope.

## Documents
- [PROJECT_REQUIREMENTS.md](PROJECT_REQUIREMENTS.md): functional and non-functional requirements
- [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md): packages, nodes, interfaces, state machines, layout
- [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md): phases and verification status
- [docs/environment.md](docs/environment.md): detected environment and install paths
- [docs/development_log.md](docs/development_log.md): what was built, tested and fixed per phase

## Repository layout
```
├── PROJECT_REQUIREMENTS.md / SYSTEM_ARCHITECTURE.md / DEVELOPMENT_PLAN.md
├── docs/                 detailed design, workflow, testing, user manual
├── env/                  RoboStack environment definition
├── ros2_ws/src/          ROS 2 packages (uco_*)
├── scripts/              environment, run, test and plotting helpers
├── tests/                system / simulation test runners
└── project_report/       engineering report, figures, recorded results
```

## Quick start (environment)
```bash
scripts/install_environment.sh apt         # Ubuntu 24.04 with access to packages.ros.org
# or
scripts/install_environment.sh robostack   # conda-based alternative
scripts/check_environment.sh
```

Build, run and test instructions are added as each phase is completed.
