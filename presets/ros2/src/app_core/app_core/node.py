"""ROS 2 нода: /input (Float64) → core → /result (Float64). Только обёртка над core."""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64

from app_core.core import Smoother


class AppNode(Node):
    def __init__(self) -> None:
        super().__init__("app_node")
        self.core = Smoother(alpha=self.declare_parameter("alpha", 0.5).value)
        self.pub = self.create_publisher(Float64, "/result", 10)
        self.create_subscription(Float64, "/input", self.on_input, 10)
        self.get_logger().info("app_node started")

    def on_input(self, msg: Float64) -> None:
        self.pub.publish(Float64(data=self.core.update(msg.data)))


def main() -> None:
    rclpy.init()
    node = AppNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
