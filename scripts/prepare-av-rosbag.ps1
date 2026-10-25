# Download CostNav public rosbag2, trim camera/cmd_vel/lidar into data/inbox/M-AVBAG-001.
docker compose exec worker python -m ugv_mlops.cli prepare-av-rosbag --max-seconds 12
