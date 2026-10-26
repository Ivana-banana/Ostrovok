#!/usr/bin/env bash
# Внутри контейнера стенда (docker/jury-stand.sh). На хосте не запускать.
set -o pipefail   # без -u: setup.bash ROS обращается к неопределённым переменным
source /opt/ros/humble/setup.bash
mkdir -p /tmp/ws && cp -r /src /tmp/ws/src && cd /tmp/ws
export COLCON_DEFAULTS_FILE=/stand/colcon-defaults.yaml
echo "== colcon build (сеть отключена)"
colcon build --event-handlers console_cohesion+ > /out/build.log 2>&1 || { tail -20 /out/build.log; echo "СБОРКА УПАЛА"; exit 1; }
source install/setup.bash
echo "== нода по launch"
ros2 launch app_core app.launch.py > /out/node.log 2>&1 &
NODE=$!
sleep 4
if [ -d /bag ]; then
  echo "== ros2 bag play"
  timeout 60 ros2 topic echo --once /result > /out/result.txt 2>&1 &
  ECHO=$!
  ros2 bag play /bag > /out/play.log 2>&1
  wait $ECHO; got=$?
else
  echo "== bag не дан: публикую один вход"
  timeout 20 ros2 topic echo --once /result > /out/result.txt 2>&1 &
  ECHO=$!
  sleep 1
  ros2 topic pub --once /input std_msgs/msg/Float64 "{data: 1.0}" > /dev/null
  wait $ECHO; got=$?
fi
# Фоновый launch в неинтерактивном bash игнорирует SIGINT: TERM, без wait —
# остальное убьёт выход контейнера.
kill -TERM $NODE 2>/dev/null
grep -q "started" /out/node.log || { echo "нода не стартовала"; cat /out/node.log; exit 1; }
[ $got = 0 ] && grep -q "data:" /out/result.txt || { echo "нода не ответила на /result"; cat /out/node.log; exit 1; }
echo "== нода ответила: $(grep data: /out/result.txt | head -1)"
