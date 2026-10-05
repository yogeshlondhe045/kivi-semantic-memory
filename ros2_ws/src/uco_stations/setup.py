from glob import glob

from setuptools import setup

package_name = 'uco_stations'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/config', glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    extras_require={'test': ['pytest']},
    zip_safe=True,
    maintainer='UCO Warehouse Team',
    maintainer_email='dev@example.com',
    description='Receiving and dispatch stations',
    license='Apache-2.0',
    entry_points={'console_scripts': [
        'receiving_station = uco_stations.receiving_station:main',
        'weighing_station = uco_stations.weighing_station:main',
        'inspection_station = uco_stations.inspection_station:main',
        'dispatch_manager = uco_stations.dispatch_manager:main',
    ]},
)
