"""Publisher adapters."""

from santiszr.infra.publisher.douyin import DouyinPublisher
from santiszr.infra.publisher.wechat_channels import WechatChannelsPublisher
from santiszr.infra.publisher.xiaohongshu import XiaohongshuPublisher

__all__ = ["DouyinPublisher", "WechatChannelsPublisher", "XiaohongshuPublisher"]
