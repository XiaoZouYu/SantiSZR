import os
import subprocess
from pathlib import Path
from typing import Union
import cv2
import torch
from insightface.app import FaceAnalysis
import torch.nn.functional as F
import numpy as np
from einops import rearrange
import kornia
from tqdm import tqdm
import soundfile as sf
import onnxruntime as ort
import gc

from transformers import (
    Wav2Vec2FeatureExtractor,
    HubertModel
)

__all__ = ["LstmSync"]
__dir__ = []

DEFAULT_MAX_REFERENCE_EDGE = 720
DEFAULT_OPENCV_THREADS = 2


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _configure_opencv_threads() -> int:
    threads = _env_int("SANTISZR_TUILIONNX_OPENCV_THREADS", DEFAULT_OPENCV_THREADS)
    try:
        cv2.setNumThreads(threads)
        return int(cv2.getNumThreads())
    except Exception:
        return threads


def _make_cuda_session_options():
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return options


def _quiet_ort_provider_logs() -> None:
    try:
        ort.set_default_logger_severity(3)
    except Exception:
        pass


def _assert_ort_cuda_session(session, *, label: str) -> None:
    providers = list(session.get_providers())
    if not providers or providers[0] != "CUDAExecutionProvider":
        raise RuntimeError(
            f"{label} is not using CUDAExecutionProvider as the primary execution provider. "
            f"Active providers: {providers}. Full CPU fallback is not allowed."
        )


def _assert_torch_module_on_cuda(module, *, label: str) -> None:
    try:
        parameter = next(module.parameters())
    except StopIteration as exc:
        raise RuntimeError(f"{label} has no parameters to verify CUDA placement.") from exc
    if parameter.device.type != "cuda":
        raise RuntimeError(
            f"{label} is on {parameter.device}, expected CUDA. CPU inference is not allowed."
        )


def _cuda_summary() -> str:
    if not torch.cuda.is_available():
        return "unavailable"
    index = torch.cuda.current_device()
    name = torch.cuda.get_device_name(index)
    return f"{name} (cuda:{index}, torch CUDA {torch.version.cuda})"


def _resize_frame_to_max_edge(frame: np.ndarray, max_edge: int) -> np.ndarray:
    if max_edge <= 0:
        return frame
    height, width = frame.shape[:2]
    current_edge = max(height, width)
    if current_edge <= max_edge:
        return frame
    scale = max_edge / float(current_edge)
    target_width = max(2, int(round(width * scale)))
    target_height = max(2, int(round(height * scale)))
    return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)


def get_video_fps(video_path):
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return int(fps or 0)


def _resize_chw_tensor(image: torch.Tensor, size: int) -> torch.Tensor:
    return F.interpolate(
        image.unsqueeze(0).to(dtype=torch.float32),
        size=(size, size),
        mode="bicubic",
        align_corners=False,
    ).squeeze(0)


def _resize_prediction_tensor(image: torch.Tensor, height: int, width: int) -> torch.Tensor:
    return F.interpolate(
        image.unsqueeze(0).to(dtype=torch.float32),
        size=(height, width),
        mode="bicubic",
        align_corners=False,
    ).squeeze(0)

###########################切脸处理
def save_debug_image(img_tensor, path, is_mask=True):
    """将 tensor תΪ numpy 并保存为图像"""
    if isinstance(img_tensor, torch.Tensor):
        img = img_tensor.squeeze().cpu().numpy()
    else:
        img = img_tensor.copy()

    if is_mask:
        # 二值或概率 mask 可视化
        img = (img * 255).astype(np.uint8)
        # 添加颜色映射更清晰
        img_color = cv2.applyColorMap(img, cv2.COLORMAP_JET)
        cv2.imwrite(path, img_color)
    else:
        # 图像数据
        img = (img.transpose(1, 2, 0) * 255).astype(np.uint8)
        cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))


class AlignRestore(object):
    def __init__(self, align_points=3, resolution=256, device="cpu", dtype=torch.float32):
        if align_points == 3:
            self.upscale_factor = 1
            ratio = resolution / 256 * 2.8
            self.crop_ratio = (ratio, ratio)
            self.face_template = np.array([[19 - 2, 30 - 10], [56 + 2, 30 - 10], [37.5, 45 - 5]])
            self.face_template = self.face_template * ratio
            self.face_size = (int(75 * self.crop_ratio[0]), int(100 * self.crop_ratio[1]))
            self.p_bias = None
            self.device = device
            self.dtype = dtype
            self.fill_value = torch.tensor([127, 127, 127], device=device, dtype=dtype)
            self.mask = torch.ones((1, 1, self.face_size[1], self.face_size[0]), device=device, dtype=dtype)

    def align_warp_face(self, img, landmarks3, smooth=True):
        affine_matrix, self.p_bias = self.transformation_from_points(
            landmarks3, self.face_template, smooth, self.p_bias
        )

        img = rearrange(torch.from_numpy(img).to(device=self.device, dtype=self.dtype), "h w c -> c h w").unsqueeze(0)
        affine_matrix = torch.from_numpy(affine_matrix).to(device=self.device, dtype=self.dtype).unsqueeze(0)

        cropped_face = kornia.geometry.transform.warp_affine(
            img,
            affine_matrix,
            (self.face_size[1], self.face_size[0]),
            mode="bilinear",
            padding_mode="fill",
            fill_value=self.fill_value,
        )
        cropped_face = rearrange(cropped_face.squeeze(0), "c h w -> h w c").cpu().numpy().astype(np.uint8)
        return cropped_face, affine_matrix

    def restore_img(self, input_img, face, affine_matrix, scale_h=1, scale_w=1):
        h, w, _ = input_img.shape

        if isinstance(affine_matrix, np.ndarray):
            affine_matrix = torch.from_numpy(affine_matrix).to(device=self.device, dtype=self.dtype).unsqueeze(0)

        inv_affine_matrix = kornia.geometry.transform.invert_affine_transform(affine_matrix)
        face = face.to(dtype=self.dtype).unsqueeze(0)

        inv_face = kornia.geometry.transform.warp_affine(
            face, inv_affine_matrix, (h, w), mode="bilinear", padding_mode="fill", fill_value=self.fill_value
        ).squeeze(0)
        # inv_face = (inv_face / 2 + 0.5).clamp(0, 1) * 255
        inv_face = inv_face.clamp(0, 1) * 255

        input_img = rearrange(torch.from_numpy(input_img).to(device=self.device, dtype=self.dtype), "h w c -> c h w")
        inv_mask = kornia.geometry.transform.warp_affine(
            self.mask, inv_affine_matrix, (h, w), padding_mode="zeros"
        )  # (1, 1, h_up, w_up)

        inv_mask_erosion = kornia.morphology.erosion(
            inv_mask,
            torch.ones(
                (int(2 * self.upscale_factor), int(2 * self.upscale_factor)), device=self.device, dtype=self.dtype
            ),
        )

        inv_mask_erosion_t = inv_mask_erosion.squeeze(0).expand_as(inv_face)
        pasted_face = inv_mask_erosion_t * inv_face
        total_face_area = torch.sum(inv_mask_erosion.float())
        w_edge = int(total_face_area ** 0.5) // 20
        erosion_radius = w_edge * 2

        # Run on CPU to avoid consuming a large amount of GPU memory.
        inv_mask_erosion = inv_mask_erosion.squeeze().cpu().numpy().astype(np.float32)
        inv_mask_center = cv2.erode(inv_mask_erosion,
                                    np.ones((int(erosion_radius * scale_h), int(erosion_radius * scale_w)), np.uint8))
        inv_mask_center = torch.from_numpy(inv_mask_center).to(device=self.device, dtype=self.dtype)[None, None, ...]

        blur_size = w_edge * 2 + 1
        sigma = 0.3 * ((blur_size - 1) * 0.5 - 1) + 0.8
        inv_soft_mask = kornia.filters.gaussian_blur2d(
            inv_mask_center, (blur_size, blur_size), (sigma, sigma)
        ).squeeze(0)
        inv_soft_mask_3d = inv_soft_mask.expand_as(inv_face)
        img_back = inv_soft_mask_3d * pasted_face + (1 - inv_soft_mask_3d) * input_img

        img_back = rearrange(img_back, "c h w -> h w c").contiguous().to(dtype=torch.uint8)
        img_back = img_back.cpu().numpy()
        return img_back

    def transformation_from_points(self, points1: torch.Tensor, points0: torch.Tensor, smooth=True, p_bias=None):
        if isinstance(points0, np.ndarray):
            points2 = torch.tensor(points0, device=self.device, dtype=torch.float32)
        else:
            points2 = points0.clone()

        if isinstance(points1, np.ndarray):
            points1_tensor = torch.tensor(points1, device=self.device, dtype=torch.float32)
        else:
            points1_tensor = points1.clone()

        c1 = torch.mean(points1_tensor, dim=0)
        c2 = torch.mean(points2, dim=0)

        points1_centered = points1_tensor - c1
        points2_centered = points2 - c2

        s1 = torch.std(points1_centered)
        s2 = torch.std(points2_centered)

        points1_normalized = points1_centered / s1
        points2_normalized = points2_centered / s2

        covariance = torch.matmul(points1_normalized.T, points2_normalized)
        U, S, V = torch.svd(covariance)

        R = torch.matmul(V, U.T)

        det = torch.det(R)
        if det < 0:
            V[:, -1] = -V[:, -1]
            R = torch.matmul(V, U.T)

        sR = (s2 / s1) * R
        T = c2.reshape(2, 1) - (s2 / s1) * torch.matmul(R, c1.reshape(2, 1))

        M = torch.cat((sR, T), dim=1)

        if smooth:
            bias = points2_normalized[2] - points1_normalized[2]
            if p_bias is None:
                p_bias = bias
            else:
                bias = p_bias * 0.2 + bias * 0.8
            p_bias = bias
            M[:, 2] = M[:, 2] + bias

        return M.cpu().numpy(), p_bias


INSIGHTFACE_DETECT_SIZE = 512


class FaceDetector:
    def __init__(self, device="cuda", auxiliary_root: str | Path | None = None):
        auxiliary_path = Path(auxiliary_root).expanduser().resolve() if auxiliary_root else None
        if auxiliary_path is None or not auxiliary_path.exists():
            raise RuntimeError(f"TuiliONNX auxiliary model directory is missing: {auxiliary_path}")

        self.app = FaceAnalysis(
            allowed_modules=["detection", "landmark_2d_106"],
            root=str(auxiliary_path),
            providers=["CUDAExecutionProvider"],
        )
        self.app.prepare(ctx_id=cuda_to_int(device), det_size=(INSIGHTFACE_DETECT_SIZE, INSIGHTFACE_DETECT_SIZE))
        for name, model in getattr(self.app, "models", {}).items():
            session = getattr(model, "session", None)
            if session is None or not hasattr(session, "get_providers"):
                continue
            _assert_ort_cuda_session(session, label=f"InsightFace {name} model")

    def __call__(self, frame, threshold=0.5):
        f_h, f_w, _ = frame.shape

        faces = self.app.get(frame)

        get_face_store = None
        max_size = 0

        if len(faces) == 0:
            return None, None
        else:
            for face in faces:
                bbox = face.bbox.astype(np.int_).tolist()
                w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
                if w < 50 or h < 80:
                    continue
                if w / h > 1.5 or w / h < 0.2:
                    continue
                if face.det_score < threshold:
                    continue
                size_now = w * h

                if size_now > max_size:
                    max_size = size_now
                    get_face_store = face

        if get_face_store is None:
            return None, None
        else:
            face = get_face_store
            lmk = np.round(face.landmark_2d_106).astype(np.int_)

            halk_face_coord = np.mean([lmk[74], lmk[73]], axis=0)  # lmk[73]

            sub_lmk = lmk[LMK_ADAPT_ORIGIN_ORDER]
            halk_face_dist = np.max(sub_lmk[:, 1]) - halk_face_coord[1]
            upper_bond = halk_face_coord[1] - halk_face_dist  # *0.94

            x1, y1, x2, y2 = (np.min(sub_lmk[:, 0]), int(upper_bond), np.max(sub_lmk[:, 0]), np.max(sub_lmk[:, 1]))

            if y2 - y1 <= 0 or x2 - x1 <= 0 or x1 < 0:
                x1, y1, x2, y2 = face.bbox.astype(np.int_).tolist()

            y2 += int((x2 - x1) * 0.1)
            x1 -= int((x2 - x1) * 0.05)
            x2 += int((x2 - x1) * 0.05)

            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(f_w, x2)
            y2 = min(f_h, y2)

            return (x1, y1, x2, y2), lmk


def cuda_to_int(cuda_str: str) -> int:
    """
    Convert the string with format "cuda:X" to integer X.
    """
    if cuda_str == "cuda":
        return 0
    device = torch.device(cuda_str)
    if device.type != "cuda":
        raise ValueError(f"Device type must be 'cuda', got: {device.type}")
    return device.index


LMK_ADAPT_ORIGIN_ORDER = [
    1,
    10,
    12,
    14,
    16,
    3,
    5,
    7,
    0,
    23,
    21,
    19,
    32,
    30,
    28,
    26,
    17,
    43,
    48,
    49,
    51,
    50,
    102,
    103,
    104,
    105,
    101,
    73,
    74,
    86,
]


#################################
def load_fixed_mask(resolution: int, mask_image) -> torch.Tensor:
    mask_image = cv2.cvtColor(mask_image, cv2.COLOR_BGR2RGB)
    mask_image = cv2.resize(mask_image, (resolution, resolution), interpolation=cv2.INTER_LANCZOS4) / 255.0
    mask_image = rearrange(torch.from_numpy(mask_image), "h w c -> c h w")
    return mask_image


class ImageProcessor:
    def __init__(
        self,
        resolution: int = 512,
        device: str = "cpu",
        mask_image=None,
        auxiliary_root: str | Path | None = None,
        dtype=torch.float32,
    ):
        self.resolution = resolution
        self.restorer = AlignRestore(resolution=resolution, device=device, dtype=dtype)

        if mask_image is None:
            self.mask_image = load_fixed_mask(resolution)
        else:
            self.mask_image = mask_image

        if device == "cpu":
            self.face_detector = None
        else:
            self.face_detector = FaceDetector(device=device, auxiliary_root=auxiliary_root)

    def affine_transform(self, image: torch.Tensor) -> np.ndarray:
        if self.face_detector is None:
            raise NotImplementedError("Using the CPU for face detection is not supported")
        bbox, landmark_2d_106 = self.face_detector(image)
        if bbox is None:
            raise RuntimeError("Face not detected")

        pt_left_eye = np.mean(landmark_2d_106[[43, 48, 49, 51, 50]], axis=0)  # left eyebrow center
        pt_right_eye = np.mean(landmark_2d_106[101:106], axis=0)  # right eyebrow center
        pt_nose = np.mean(landmark_2d_106[[74, 77, 83, 86]], axis=0)  # nose center

        landmarks3 = np.round([pt_left_eye, pt_right_eye, pt_nose])

        face, affine_matrix = self.restorer.align_warp_face(image.copy(), landmarks3=landmarks3, smooth=True)
        box = [0, 0, face.shape[1], face.shape[0]]  # x1, y1, x2, y2
        face = cv2.resize(face, (self.resolution, self.resolution), interpolation=cv2.INTER_LANCZOS4)
        face = rearrange(torch.from_numpy(face), "h w c -> c h w")
        return face, box, affine_matrix

    def preprocess_fixed_mask_image(self, image: torch.Tensor, affine_transform=False):
        if affine_transform:
            image, _, _ = self.affine_transform(image)
        else:
            image = _resize_chw_tensor(image, self.resolution)
        pixel_values = image / 255.0
        masked_pixel_values = pixel_values * self.mask_image
        return pixel_values, masked_pixel_values, self.mask_image[0:1]

    def prepare_masks_and_masked_images(self, images: Union[torch.Tensor, np.ndarray], affine_transform=False):
        if isinstance(images, np.ndarray):
            images = torch.from_numpy(images)
        if images.shape[3] == 3:
            images = rearrange(images, "f h w c -> f c h w")

        results = [self.preprocess_fixed_mask_image(image, affine_transform=affine_transform) for image in images]

        pixel_values_list, masked_pixel_values_list, masks_list = list(zip(*results))
        return torch.stack(pixel_values_list), torch.stack(masked_pixel_values_list), torch.stack(masks_list)

    def process_images(self, images: Union[torch.Tensor, np.ndarray]):
        if isinstance(images, np.ndarray):
            images = torch.from_numpy(images)
        if images.shape[3] == 3:
            images = rearrange(images, "f h w c -> f c h w")
        resized = [_resize_chw_tensor(image, self.resolution) for image in images]
        return torch.stack(resized) / 255.0

# def get_filename_without_ext(filepath):
#     # 获取文件名（包含扩展名）
#     filename_with_ext = os.path.basename(filepath)
#     # 分离文件名和扩展名
#     filename_without_ext = os.path.splitext(filename_with_ext)[0]
#     return filename_without_ext

def get_filename_without_ext(filepath):
    # 获取文件名（包含扩展名）
    filename_with_ext = os.path.basename(filepath)
    # 分离文件名和扩展名
    filename_without_ext = os.path.splitext(filename_with_ext)[0]
    
    # 处理带有后缀的文件名（如 256_m -> 256）
    # 如果文件名包含下划线，只取第一部分作为数字
    if '_' in filename_without_ext:
        filename_without_ext = filename_without_ext.split('_')[0]
    
    # 确保返回的是纯数字字符串
    # 如果包含非数字字符，尝试提取数字部分
    import re
    numbers = re.findall(r'\d+', filename_without_ext)
    if numbers:
        return numbers[0]  # 返回第一个找到的数字
    else:
        # 如果没有找到数字，返回默认值256
        print(f"警告：无法从文件名 {filepath} 中提取数字，使用默认值256")
        return "256"

##########################图像处理
class LstmSync():
    def __init__(
            self,
            human_path: str = None,
            hubert_path: str = None,
            checkpoints_root: str | Path | None = None,
            batch_size: int = 4,
            sync_offset: float = 0,
            scale_h: float = 1.,
            scale_w: float = 1.,
            compress_inference_check_box: bool = False,
            ffmpeg_path: str = "ffmpeg",
            video_encoder: str = "h264_nvenc",
    ):

        if human_path is None or hubert_path is None:
            raise RuntimeError("TuiliONNX model paths are required.")
        if not torch.cuda.is_available():
            raise RuntimeError("TuiliONNX GPU runtime is unavailable: CUDA is not available.")
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            raise RuntimeError("TuiliONNX GPU runtime is unavailable: ONNX Runtime CUDAExecutionProvider is missing.")

        self.human_path = str(Path(human_path).expanduser().resolve())
        self.hubert_path = str(Path(hubert_path).expanduser().resolve())
        self.checkpoints_root = (
            Path(checkpoints_root).expanduser().resolve()
            if checkpoints_root is not None
            else Path(self.human_path).resolve().parent
        )
        if not Path(self.human_path).exists():
            raise RuntimeError(f"TuiliONNX human model is missing: {self.human_path}")
        if not Path(self.hubert_path).exists():
            raise RuntimeError(f"TuiliONNX hubert model directory is missing: {self.hubert_path}")

        try:
            self.face_size = int(get_filename_without_ext(self.human_path))
        except Exception as exc:
            raise RuntimeError(f"Unable to infer TuiliONNX face size from model name: {self.human_path}") from exc

        repair_npy_path = self.checkpoints_root / "repair.npy"
        if not repair_npy_path.exists():
            raise RuntimeError(f"TuiliONNX repair mask is missing: {repair_npy_path}")

        self.use_cuda = True
        self.wav2lip_batch_size = batch_size
        self.syncnet_T = 16
        self.hparams_img_size = self.face_size
        self.audio_type = "hubert"
        self.sync_offset = float(sync_offset)
        self.mask_image_path = np.load(repair_npy_path)
        self.scale_h = scale_h
        self.scale_w = scale_w
        self.compress_inference_check_box = compress_inference_check_box
        self.weight_dtype = torch.float16
        self.device = 'cuda'
        self.ffmpeg_path = str(ffmpeg_path)
        self.video_encoder = str(video_encoder)
        self.opencv_threads = _configure_opencv_threads()
        self.mask_image = load_fixed_mask(self.hparams_img_size, self.mask_image_path)
        self.detect_face = ImageProcessor(
            resolution=self.hparams_img_size,
            device=self.device,
            mask_image=self.mask_image,
            auxiliary_root=self.checkpoints_root / "auxiliary",
            dtype=torch.float32,
        )
        self.ort_session = self._load_ort_session()
        self.input_names = [item.name for item in self.ort_session.get_inputs()]
        self.model_dtype = np.float16 if "float16" in self.ort_session.get_inputs()[0].type else np.float32
        self.feature_extractor, self.audio_model = self._load_audio_model()
        self.assert_gpu_runtime()

    def _load_ort_session(self):
        _quiet_ort_provider_logs()
        session = ort.InferenceSession(
            self.human_path,
            sess_options=_make_cuda_session_options(),
            providers=["CUDAExecutionProvider"],
        )
        _assert_ort_cuda_session(session, label="TuiliONNX lip-sync ONNX model")
        return session

    def _load_audio_model(self):
        feature_extractor = Wav2Vec2FeatureExtractor.from_pretrained(self.hubert_path)
        model = HubertModel.from_pretrained(self.hubert_path)
        model = model.to(self.device)
        model = model.half()
        model.eval()
        _assert_torch_module_on_cuda(model, label="TuiliONNX Hubert audio model")
        return feature_extractor, model

    def assert_gpu_runtime(self) -> None:
        if not torch.cuda.is_available():
            raise RuntimeError("TuiliONNX GPU runtime is unavailable: CUDA is not available.")
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            raise RuntimeError("TuiliONNX GPU runtime is unavailable: ONNX Runtime CUDAExecutionProvider is missing.")
        _assert_ort_cuda_session(self.ort_session, label="TuiliONNX lip-sync ONNX model")
        _assert_torch_module_on_cuda(self.audio_model, label="TuiliONNX Hubert audio model")

    def runtime_notes(self) -> list[str]:
        return [
            f"TuiliONNX GPU verified: {_cuda_summary()}.",
            "TuiliONNX ONNX Runtime providers: "
            f"{', '.join(self.ort_session.get_providers())}; CUDAExecutionProvider is primary.",
            "TuiliONNX media pipeline: "
            f"FFmpeg encoder={self.video_encoder}, OpenCV threads={self.opencv_threads}.",
        ]

    def _run_ffmpeg(self, command: list[str]) -> None:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"FFmpeg command failed with exit code {completed.returncode}: {' '.join(command)}\n"
                f"{completed.stderr.strip()}"
            )

    def __face_detect(self, images):
        results = []
        missing_indices = []
        for i in tqdm(range(0, len(images))):
            frame = cv2.cvtColor(images[i], cv2.COLOR_BGR2RGB)
            try:
                face, box, affine_matrix = self.detect_face.affine_transform(frame)
                face = rearrange(face.cpu().numpy(), "c h w -> h w c")
                face = face[..., ::-1]  # to rgb
                results.append([face, box, affine_matrix, True])
            except Exception:
                if not missing_indices:
                    try:
                        noface_dir = Path("temp")
                        noface_dir.mkdir(parents=True, exist_ok=True)
                        cv2.imwrite(str(noface_dir / "noface.jpg"), images[i])
                    except Exception:
                        pass
                missing_indices.append(i)
                results.append([None, None, None, False])

        first_valid = next((item for item in results if item[3]), None)
        if first_valid is None:
            raise RuntimeError(
                "No face detected in any reference frame. Check temp/noface.jpg and use a clearer front-facing reference video."
            )

        normalized_results = []
        last_valid = None
        for item in results:
            if item[3]:
                last_valid = item
                normalized_results.append(item)
                continue

            fallback = last_valid or first_valid
            normalized_results.append([fallback[0], fallback[1], fallback[2], False])

        if missing_indices:
            print(
                "Face detection missed "
                f"{len(missing_indices)}/{len(images)} reference frames; "
                "those frames will be kept unchanged in the output video."
            )

        return normalized_results

    def __datagen(self, frames, mels):
        img_batch, mel_batch, frame_batch, coords_batch, affines_batch = [], [], [], [], []

        face_det_results = self.__face_detect(frames)  # BGR2RGB for CNN face detection

        for i, m in enumerate(mels):
            n_frames = len(frames)
            idx = i % n_frames
            frame_to_save = frames[idx].copy()
            face, coords, affine_matrix, has_face = face_det_results[idx]
            face = cv2.cvtColor(face, cv2.COLOR_BGR2RGB)
            img_batch.append(face)
            mel_batch.append(m)
            frame_batch.append(frame_to_save)
            coords_batch.append(coords if has_face else None)
            affines_batch.append(affine_matrix)

            if len(img_batch) >= self.wav2lip_batch_size:
                img_batch, mel_batch = np.asarray(img_batch), np.asarray(mel_batch)
                ref_pixel_values, masked_pixel_values, masks = self.detect_face.prepare_masks_and_masked_images(
                    img_batch, affine_transform=False
                )
                img_batch = np.concatenate((masks, masked_pixel_values, ref_pixel_values), axis=1)

                yield img_batch, mel_batch, frame_batch, coords_batch, affines_batch
                img_batch, mel_batch, frame_batch, coords_batch, affines_batch = [], [], [], [], []

        if len(img_batch) > 0:
            img_batch, mel_batch = np.asarray(img_batch), np.asarray(mel_batch)
            ref_pixel_values, masked_pixel_values, masks = self.detect_face.prepare_masks_and_masked_images(
                img_batch, affine_transform=False
            )
            img_batch = np.concatenate((masks, masked_pixel_values, ref_pixel_values), axis=1)

            yield img_batch, mel_batch, frame_batch, coords_batch, affines_batch


    def __dir__(self):
        return ['run']


    def run(self, video_path, video_fps25_path, video_temp_path, audio_path, audio_temp_path, video_out_path, compress_inference_check_box=None):
        self.assert_gpu_runtime()
        if compress_inference_check_box is not None:
            self.compress_inference_check_box = compress_inference_check_box

        video_path = str(Path(video_path).expanduser().resolve())
        video_fps25_path = str(Path(video_fps25_path).expanduser().resolve())
        video_temp_path = str(Path(video_temp_path).expanduser().resolve())
        audio_path = str(Path(audio_path).expanduser().resolve())
        audio_temp_path = str(Path(audio_temp_path).expanduser().resolve())
        video_out_path = str(Path(video_out_path).expanduser().resolve())
        video_temp_path = video_temp_path + ".avi"

        if get_video_fps(video_path) != 25:
            print("Converting video to 25 fps...")
            self._run_ffmpeg(
                [
                    self.ffmpeg_path,
                    "-y",
                    "-i",
                    video_path,
                    "-an",
                    "-r",
                    "25",
                    "-c:v",
                    self.video_encoder,
                    "-pix_fmt",
                    "yuv420p",
                    video_fps25_path,
                ]
            )
        else:
            video_fps25_path = video_path

        video_stream = cv2.VideoCapture(video_fps25_path)
        fps = video_stream.get(cv2.CAP_PROP_FPS) or 25.0
        print('Reading video frames...')
        max_reference_edge = _env_int(
            "SANTISZR_TUILIONNX_MAX_REFERENCE_EDGE",
            DEFAULT_MAX_REFERENCE_EDGE,
        )
        full_frames = []
        try:
            while 1:
                still_reading, frame = video_stream.read()

                if not still_reading:
                    break
                frame = _resize_frame_to_max_edge(frame, max_reference_edge)
                full_frames.append(frame)
        finally:
            video_stream.release()
            video_stream = None

        print("Loaded {} reference frames from full video.".format(len(full_frames)))

        if not full_frames:
            raise RuntimeError(f"No readable frames found in reference video: {video_fps25_path}")

        self._run_ffmpeg(
            [
                self.ffmpeg_path,
                "-y",
                "-i",
                audio_path,
                "-ac",
                "1",
                "-ar",
                "16000",
                audio_temp_path,
            ]
        )

        if self.audio_type == "hubert":
            wav, sr = sf.read(audio_temp_path)
            input_values = self.feature_extractor(wav, sampling_rate=sr, return_tensors="pt").input_values
            input_values = input_values.half()
            input_values = input_values.to(self.device)

            with torch.no_grad():
                outputs = self.audio_model(input_values)
                repst = outputs.last_hidden_state.permute(0, 2, 1)
                repst = repst.cpu().numpy()

            rep_step_size = 10
            rep_chunks = []
            rep_idx_multiplier = 50. / fps
            i = 0

            while 1:
                start_idx = int(max(i + self.sync_offset, 0) * rep_idx_multiplier)
                # start_idx = int(i * rep_idx_multiplier)
                if start_idx + rep_step_size > repst.shape[-1]:
                    rep_chunks.append(repst[0, :, repst.shape[-1] - rep_step_size:])
                    break
                rep_chunks.append(repst[0, :, start_idx: start_idx + rep_step_size])
                i += 1

        else:
            raise Exception("no audio_type")

        print("Length of rep chunks: {}".format(len(rep_chunks)))
        face_det_results = self.__datagen(full_frames, rep_chunks)
        
        frame_h, frame_w = full_frames[0].shape[:-1]
        out = cv2.VideoWriter(video_temp_path, cv2.VideoWriter_fourcc(*'DIVX'), fps, (frame_w, frame_h))
        wav2lip_batch_size = self.wav2lip_batch_size

        for i, (img_batch, mel_batch, frames, coords, affines) in enumerate(tqdm(face_det_results,
                                                                                 total=int(
                                                                                     np.ceil(
                                                                                         float(
                                                                                             len(full_frames)) / wav2lip_batch_size)))):

            mel_batch = np.transpose(mel_batch, (0, 2, 1))
            b = mel_batch.shape[0]
            g_frames = []
            for frame in range(b):

                if (i == 0 and frame == 0) or ((i * wav2lip_batch_size + frame + 1) % self.syncnet_T == 0):
                    # 初始化时间步，使用动态检测的数据类型
                    hn = np.zeros((2, 1, 576), dtype=self.model_dtype)
                    cn = np.zeros((2, 1, 576), dtype=self.model_dtype)

                x_frame = img_batch[frame, :, :, :].astype(self.model_dtype)
                x_frame = np.expand_dims(x_frame, axis=0)
                indiv_frame = mel_batch[frame, :, :].astype(self.model_dtype)
                indiv_frame = np.expand_dims(indiv_frame, axis=0)
                g, hn, cn = self.ort_session.run(None, {
                    self.input_names[0]: indiv_frame,
                    self.input_names[1]: x_frame,
                    self.input_names[2]: hn,
                    self.input_names[3]: cn,
                })

                g_frames.append(g.squeeze().astype(np.float32))

            pred = np.stack(g_frames, axis=0)

            for p, f, c, a in zip(pred, frames, coords, affines):
                if c is None:
                    out.write(f)
                    continue
                x1, y1, x2, y2 = c
                height = int(y2 - y1)
                width = int(x2 - x1)
                p = p[[2, 1, 0], :, :]
                p = _resize_prediction_tensor(torch.from_numpy(p).to(self.device), height, width)
                f = self.detect_face.restorer.restore_img(f, p, a, scale_h=self.scale_h, scale_w=self.scale_w)
                out.write(f)
            
            # 定期清理内存，每处理10个批次清理一次
            if (i + 1) % 10 == 0:
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

        if out is not None:
            out.release()
            out = None
        del face_det_results
        del full_frames
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        try:
            self._run_ffmpeg(
                [
                    self.ffmpeg_path,
                    "-y",
                    "-i",
                    video_temp_path,
                    "-i",
                    audio_temp_path,
                    "-c:v",
                    self.video_encoder,
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-ar",
                    "44100",
                    "-ac",
                    "2",
                    "-shortest",
                    video_out_path,
                ]
            )
        finally:
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
        return video_out_path
