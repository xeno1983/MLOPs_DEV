from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    catalog_database_url: str = "postgresql+psycopg://ugv:ugv_change_me@localhost:5432/catalog"
    redis_url: str = "redis://localhost:6379/0"
    minio_endpoint: str = "localhost:9000"
    minio_root_user: str = "GKabcdef0123456789abcdef01234567"
    minio_root_password: str = "ugv_garage_secret_change_me_do_not_use_in_prod"
    minio_secure: bool = False
    s3_region: str = "garage"
    mlflow_tracking_uri: str = "http://localhost:5000"
    artifact_policy_path: str = "configs/artifact_policy.yaml"
    signing_key_path: str = "data/keys/ed25519.pem"
    signing_pub_path: str = "data/keys/ed25519.pub"
    default_learning_stage: str = "S3"
    inbox_dir: str = "data/inbox"
    work_dir: str = "data/work"
    actor: str = "system"
    label_studio_url: str = "http://labelstudio:8080"
    label_studio_username: str = "admin@ugv.local"
    label_studio_password: str = "label_change_me"
    label_studio_api_key: str = ""
    sample_public_base: str = "http://localhost:8000"
    lakefs_url: str = "http://lakefs:8000"
    lakefs_access_key_id: str = "AKIAUGVLAKEFS01"
    lakefs_secret_access_key: str = "lakefs_secret_change_me_32chars!"
    gitea_url: str = "http://gitea:3000"
    grafana_url: str = "http://grafana:3000"
    prometheus_url: str = "http://prometheus:9090"
    rosbag_topics: str = ""
    rosbag_frame_stride: int = 5
    rosbag_max_frames_per_topic: int = 400
    rosbag_lidar_stride: int = 20
    rosbag_max_lidar_keyframes: int = 80
    rosbag_max_lidar_points: int = 80000
    e2e_action_max_dt_sec: float = 0.25
    e2e_history: int = 4
    yolox_weights_path: str = "data/models/yolox_nano.pth"
    yolox_cache_dir: str = "data/cache"
    yolox_imgsz: int = 416
    yolox_conf_thre: float = 0.20
    yolox_train_steps: int = 8
    yolox_max_train_images: int = 24
    dvc_root: str = ""
    annotate_dir: str = "data/annotate"
    autoware_e2e_root: str = r"C:\Users\xeno1\Autoware_e2e"
    xtreme1_url: str = "http://localhost:8190"

    @property
    def inbox_path(self) -> Path:
        return Path(self.inbox_dir)

    @property
    def work_path(self) -> Path:
        return Path(self.work_dir)


settings = Settings()
