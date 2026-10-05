// clock_throttle: republish the Gazebo simulation clock at a reduced rate.
//
// Gazebo publishes its clock on every physics step (500 Hz of simulation time). Every ROS node
// that uses simulation time subscribes to /clock, and for the ~15 Python nodes of this project
// processing 500 messages per second each starved the simulator (real-time factor fell to 0.1).
// This node receives the full-rate clock on /clock_gz and republishes /clock at `rate_hz` of
// simulation time (launch default 25 Hz = 40 ms resolution, finer than the fastest loop here,
// the 20 Hz velocity smoother). Each /clock message wakes every ROS node using sim time.
#include <rclcpp/rclcpp.hpp>
#include <rosgraph_msgs/msg/clock.hpp>

class ClockThrottle : public rclcpp::Node
{
public:
  ClockThrottle()
  : Node("clock_throttle")
  {
    const double rate = declare_parameter("rate_hz", 50.0);
    period_ns_ = static_cast<int64_t>(1e9 / rate);
    auto qos = rclcpp::ClockQoS();
    pub_ = create_publisher<rosgraph_msgs::msg::Clock>("/clock", qos);
    sub_ = create_subscription<rosgraph_msgs::msg::Clock>(
      "/clock_gz", qos, [this](rosgraph_msgs::msg::Clock::ConstSharedPtr msg) {
        const int64_t t = static_cast<int64_t>(msg->clock.sec) * 1000000000LL + msg->clock.nanosec;
        // publish when enough simulation time has passed, or when the clock jumped back (reset)
        if (last_ns_ < 0 || t - last_ns_ >= period_ns_ || t < last_ns_) {
          last_ns_ = t;
          pub_->publish(*msg);
        }
      });
    RCLCPP_INFO(get_logger(), "Republishing /clock_gz -> /clock at %.0f Hz of simulation time", rate);
  }

private:
  int64_t period_ns_{20000000};
  int64_t last_ns_{-1};
  rclcpp::Publisher<rosgraph_msgs::msg::Clock>::SharedPtr pub_;
  rclcpp::Subscription<rosgraph_msgs::msg::Clock>::SharedPtr sub_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<ClockThrottle>());
  rclcpp::shutdown();
  return 0;
}
