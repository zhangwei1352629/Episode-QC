import * as THREE from "three";
import URDFLoader from "../../node_modules/urdf-loader/src/URDFLoader.js";
import {
  chooseSupportFoot,
  g1ElbowAngleFromHumanFlexion,
  robotRootPoseInThree,
  wristAnglesFromWorldQuaternions,
} from "./g1-pose.mjs";

const MODEL_URL = new URL("./assets/unitree-g1-29dof/g1_29dof.urdf", import.meta.url).href;
const ROS_TO_THREE_X = -Math.PI / 2;

const HUMAN_TO_G1_LINK = {
  hips: "pelvis",
  pelvis: "pelvis",
  leftupleg: "left_hip_pitch_link",
  leftleg: "left_knee_link",
  leftfoot: "left_ankle_roll_link",
  lefttoe: "left_ankle_roll_link",
  rightupleg: "right_hip_pitch_link",
  rightleg: "right_knee_link",
  rightfoot: "right_ankle_roll_link",
  righttoe: "right_ankle_roll_link",
  spine1: "waist_yaw_link",
  spine2: "waist_roll_link",
  chest: "torso_link",
  neck: "head_link",
  head: "head_link",
  leftshoulder: "left_shoulder_pitch_link",
  leftarm: "left_shoulder_yaw_link",
  leftforearm: "left_elbow_link",
  lefthand: "left_rubber_hand",
  rightshoulder: "right_shoulder_pitch_link",
  rightarm: "right_shoulder_yaw_link",
  rightforearm: "right_elbow_link",
  righthand: "right_rubber_hand",
};

const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
const normalizedName = (name) => String(name || "").toLowerCase().replace(/[^a-z0-9]/g, "");

function angleAtJoint(parent, joint, child) {
  if (!parent || !joint || !child) return 0;
  const first = new THREE.Vector3().subVectors(parent, joint).normalize();
  const second = new THREE.Vector3().subVectors(child, joint).normalize();
  return Math.PI - Math.acos(clamp(first.dot(second), -1, 1));
}

function pitchRollForDownwardLimb(start, end) {
  if (!start || !end) return { pitch: 0, roll: 0 };
  const direction = new THREE.Vector3().subVectors(end, start).normalize();
  return {
    pitch: Math.atan2(-direction.x, -direction.z),
    roll: Math.atan2(direction.y, -direction.z),
  };
}

export class G1Viewer {
  constructor(canvas, onStatusChange = () => {}) {
    this.canvas = canvas;
    this.onStatusChange = onStatusChange;
    this.status = "loading";
    this.robot = null;
    this.episodeId = null;
    this.footRestPitch = { left: null, right: null };
    this.restPosition = new THREE.Vector3();
    this.poseContextKey = null;
    this.rootPlanarOriginRos = null;
    this.supportFoot = null;
    this.modelRoot = new THREE.Group();
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0b1014);
    this.scene.add(this.modelRoot);
    this.camera = new THREE.PerspectiveCamera(28, 1, 0.01, 30);
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: "high-performance" });
    this.renderer.setClearColor(0x0b1014, 1);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.12;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;

    this.scene.add(new THREE.HemisphereLight(0xdbe8ef, 0x111820, 2.25));
    const keyLight = new THREE.DirectionalLight(0xffffff, 3.2);
    keyLight.position.set(2.8, 4.5, 2.2);
    keyLight.castShadow = true;
    keyLight.shadow.mapSize.set(1024, 1024);
    this.scene.add(keyLight);
    const rimLight = new THREE.DirectionalLight(0xb9d7ff, 1.7);
    rimLight.position.set(-2.4, 2.2, -3);
    this.scene.add(rimLight);

    const floorMaterial = new THREE.MeshStandardMaterial({ color: 0x11181d, roughness: 0.92, metalness: 0.02, transparent: true, opacity: 0.82 });
    this.floor = new THREE.Mesh(new THREE.CircleGeometry(1.55, 64), floorMaterial);
    this.floor.rotation.x = -Math.PI / 2;
    this.floor.receiveShadow = true;
    this.scene.add(this.floor);
    const grid = new THREE.GridHelper(3.2, 20, 0x65727a, 0x2a343b);
    grid.material.transparent = true;
    grid.material.opacity = 0.38;
    grid.position.y = 0.002;
    this.scene.add(grid);

    this.selectionMarker = new THREE.Mesh(
      new THREE.SphereGeometry(0.045, 18, 12),
      new THREE.MeshBasicMaterial({ color: 0xd7f356, transparent: true, opacity: 0.88 }),
    );
    this.selectionMarker.visible = false;
    this.scene.add(this.selectionMarker);
    this.loadModel();
  }

  loadModel() {
    const manager = new THREE.LoadingManager();
    const loader = new URDFLoader(manager);
    loader.parseCollision = false;
    manager.onLoad = () => {
      if (!this.robot) return;
      this.robot.traverse((object) => {
        if (!object.isMesh) return;
        const sourceColor = object.material?.color || new THREE.Color(0xb7bec2);
        const brightness = sourceColor.r + sourceColor.g + sourceColor.b;
        object.material = new THREE.MeshStandardMaterial({
          color: brightness < 1.15 ? 0x2c3236 : 0xc5c9cb,
          roughness: 0.48,
          metalness: 0.12,
        });
        object.castShadow = true;
        object.receiveShadow = true;
      });
      this.robot.updateMatrixWorld(true);
      const bounds = new THREE.Box3().setFromObject(this.robot);
      const center = bounds.getCenter(new THREE.Vector3());
      this.restPosition.set(-center.x, -bounds.min.y, -center.z);
      this.modelRoot.position.copy(this.restPosition);
      this.modelRoot.updateMatrixWorld(true);
      this.status = "ready";
      this.onStatusChange("ready");
    };
    loader.load(
      MODEL_URL,
      (robot) => {
        this.robot = robot;
        robot.rotation.x = ROS_TO_THREE_X;
        this.modelRoot.add(robot);
      },
      undefined,
      (error) => {
        this.status = "error";
        this.onStatusChange("error", error);
      },
    );
  }

  render(frame, view) {
    this.resize();
    const hasFrame = Boolean(frame?.positions?.length);
    const robotAction = view?.robotAction;
    const hasRobotAction = Boolean(robotAction?.jointPositions?.length === 29);
    const poseContextKey = `${view?.episodeId || ""}:${hasRobotAction ? robotAction.source_key || "action" : "mocap"}`;
    if (view?.episodeId !== this.episodeId || poseContextKey !== this.poseContextKey) {
      this.episodeId = view?.episodeId || null;
      this.poseContextKey = poseContextKey;
      this.footRestPitch = { left: null, right: null };
      this.rootPlanarOriginRos = null;
      this.supportFoot = null;
    }
    this.modelRoot.visible = (hasRobotAction || hasFrame) && this.status === "ready";
    if (hasRobotAction && this.robot) this.applyRobotAction(robotAction);
    else if (hasFrame && this.robot) this.applyMocapPose(frame);
    this.updateCamera(view);
    this.updateSelection(view?.selectedJoint);
    this.renderer.render(this.scene, this.camera);
    return hasFrame && this.robot ? this.projectJoints(frame) : [];
  }

  applyRobotAction(frame) {
    frame.jointNames.forEach((name, index) => this.setJoint(name, frame.jointPositions[index]));
    const rootPosition = frame.rootPosition || frame.root_position;
    const rootQuaternion = frame.rootQuaternionWxyz || frame.root_quaternion_wxyz;
    if (Array.isArray(rootPosition) && rootPosition.length === 3 && !this.rootPlanarOriginRos) {
      this.rootPlanarOriginRos = [Number(rootPosition[0]), Number(rootPosition[1])];
    }
    const rootPose = robotRootPoseInThree(rootPosition, rootQuaternion, this.rootPlanarOriginRos);
    this.modelRoot.quaternion.fromArray(rootPose.quaternionXyzw);
    if (rootPose.position) {
      this.modelRoot.position.fromArray(rootPose.position);
      this.modelRoot.updateMatrixWorld(true);
    } else {
      this.modelRoot.position.set(this.restPosition.x, 0, this.restPosition.z);
      this.groundToSupportFoot();
    }
  }

  groundToSupportFoot() {
    this.modelRoot.updateMatrixWorld(true);
    const heights = {};
    for (const side of ["left", "right"]) {
      const link = this.robot?.links?.[`${side}_ankle_roll_link`];
      if (!link) continue;
      const bounds = new THREE.Box3().setFromObject(link);
      if (!bounds.isEmpty() && Number.isFinite(bounds.min.y)) heights[side] = bounds.min.y;
    }
    this.supportFoot = chooseSupportFoot(heights.left, heights.right, this.supportFoot);
    const supportHeight = heights[this.supportFoot];
    if (Number.isFinite(supportHeight)) this.modelRoot.position.y -= supportHeight;
    else this.modelRoot.position.y = this.restPosition.y;
    this.modelRoot.updateMatrixWorld(true);
  }

  resize() {
    const rectangle = this.canvas.getBoundingClientRect();
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(rectangle.width * pixelRatio));
    const height = Math.max(1, Math.round(rectangle.height * pixelRatio));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.renderer.setPixelRatio(pixelRatio);
      this.renderer.setSize(rectangle.width, rectangle.height, false);
    }
    this.camera.aspect = Math.max(0.01, rectangle.width / Math.max(1, rectangle.height));
    this.camera.updateProjectionMatrix();
  }

  updateCamera(view = {}) {
    const zoom = clamp(Number(view.cameraZoom) || 1, 0.35, 3);
    const yaw = Number(view.cameraYaw) || 0;
    const pitch = clamp(Number(view.cameraPitch) || 0, -0.8, 0.8);
    const target = new THREE.Vector3(0, 0.67, 0);
    const distance = 3.15 / zoom;
    const elevation = clamp(0.12 + pitch, -0.62, 0.92);
    const horizontal = Math.cos(elevation) * distance;
    this.camera.position.set(Math.cos(yaw) * horizontal, target.y + Math.sin(elevation) * distance, Math.sin(yaw) * horizontal);
    this.camera.lookAt(target);
  }

  applyMocapPose(frame) {
    this.modelRoot.position.set(this.restPosition.x, 0, this.restPosition.z);
    this.modelRoot.quaternion.identity();
    const points = new Map();
    const rotations = new Map();
    frame.jointNames?.forEach((name, index) => {
      if (!frame.validity?.[index]) return;
      const position = frame.positions[index];
      points.set(normalizedName(name), new THREE.Vector3(position[0], position[1], position[2]));
      const rotation = frame.rotations_wxyz?.[index];
      if (Array.isArray(rotation) && rotation.length === 4) rotations.set(normalizedName(name), rotation);
    });
    const point = (...names) => names.map((name) => points.get(normalizedName(name))).find(Boolean);
    const rotation = (...names) => names.map((name) => rotations.get(normalizedName(name))).find(Boolean);
    const leftHip = point("LeftUpLeg", "LeftHip"), rightHip = point("RightUpLeg", "RightHip");
    let bodyYaw = 0;
    if (leftHip && rightHip) {
      const lateral = new THREE.Vector3().subVectors(leftHip, rightHip);
      bodyYaw = Math.atan2(-lateral.x, lateral.y);
    }
    this.modelRoot.rotation.y = bodyYaw;
    const localize = (value) => {
      if (!value) return null;
      const cosine = Math.cos(bodyYaw), sine = Math.sin(bodyYaw);
      return new THREE.Vector3(cosine * value.x + sine * value.y, -sine * value.x + cosine * value.y, value.z);
    };
    const localPoint = (...names) => localize(point(...names));

    this.applyLeg("left", localPoint("LeftUpLeg", "LeftHip"), localPoint("LeftLeg", "LeftKnee"), localPoint("LeftFoot", "LeftAnkle"), localPoint("LeftToe"));
    this.applyLeg("right", localPoint("RightUpLeg", "RightHip"), localPoint("RightLeg", "RightKnee"), localPoint("RightFoot", "RightAnkle"), localPoint("RightToe"));
    this.applyArm("left", localPoint("LeftShoulder"), localPoint("LeftArm"), localPoint("LeftForeArm"), localPoint("LeftHand"), rotation("LeftForeArm"), rotation("LeftHand"));
    this.applyArm("right", localPoint("RightShoulder"), localPoint("RightArm"), localPoint("RightForeArm"), localPoint("RightHand"), rotation("RightForeArm"), rotation("RightHand"));

    const hips = localPoint("Hips", "Pelvis"), chest = localPoint("Chest", "Spine2", "Spine1");
    const torso = hips && chest ? new THREE.Vector3().subVectors(chest, hips).normalize() : new THREE.Vector3(0, 0, 1);
    this.setJoint("waist_pitch_joint", Math.atan2(torso.x, Math.max(1e-6, torso.z)));
    this.setJoint("waist_roll_joint", Math.atan2(-torso.y, Math.max(1e-6, torso.z)));
    this.setJoint("waist_yaw_joint", 0);
    this.groundToSupportFoot();
  }

  applyLeg(side, hip, knee, ankle, toe) {
    const upper = pitchRollForDownwardLimb(hip, knee);
    const kneeFlexion = angleAtJoint(hip, knee, ankle);
    this.setJoint(`${side}_hip_pitch_joint`, upper.pitch);
    this.setJoint(`${side}_hip_roll_joint`, upper.roll);
    this.setJoint(`${side}_hip_yaw_joint`, 0);
    this.setJoint(`${side}_knee_joint`, kneeFlexion);
    const footDirection = ankle && toe ? new THREE.Vector3().subVectors(toe, ankle).normalize() : null;
    // 人体 Foot→Toe 骨骼在平脚状态下也天然向下，不能把这个固定坡度直接当作 G1 的踝关节角。
    // 每个 Episode 首帧记录该坡度作为中立姿态，只映射后续相对变化；G1 的 +Y 轴正角会让脚尖向下。
    const sourceFootPitch = footDirection ? Math.atan2(-footDirection.z, Math.hypot(footDirection.x, footDirection.y)) : 0;
    if (footDirection && this.footRestPitch[side] === null) this.footRestPitch[side] = sourceFootPitch;
    const desiredWorldFootPitch = sourceFootPitch - (this.footRestPitch[side] ?? sourceFootPitch);
    this.setJoint(`${side}_ankle_pitch_joint`, desiredWorldFootPitch - upper.pitch - kneeFlexion);
    this.setJoint(`${side}_ankle_roll_joint`, -upper.roll);
  }

  applyArm(side, shoulder, upperArm, elbow, hand, forearmRotation, handRotation) {
    const start = upperArm || shoulder;
    const upper = pitchRollForDownwardLimb(start, elbow);
    const elbowFlexion = angleAtJoint(start, elbow, hand);
    const wrist = wristAnglesFromWorldQuaternions(side, forearmRotation, handRotation);
    this.setJoint(`${side}_shoulder_pitch_joint`, upper.pitch);
    this.setJoint(`${side}_shoulder_roll_joint`, upper.roll);
    this.setJoint(`${side}_shoulder_yaw_joint`, 0);
    // G1 的肘关节机械零位是前臂向前；人体零屈曲则是上下臂共线，因此需要补偿 90°。
    this.setJoint(`${side}_elbow_joint`, g1ElbowAngleFromHumanFlexion(elbowFlexion));
    this.setJoint(`${side}_wrist_roll_joint`, wrist.roll);
    this.setJoint(`${side}_wrist_pitch_joint`, wrist.pitch);
    this.setJoint(`${side}_wrist_yaw_joint`, wrist.yaw);
  }

  setJoint(name, value) {
    const joint = this.robot?.joints?.[name];
    if (!joint || !Number.isFinite(value)) return;
    const lower = Number.isFinite(joint.limit?.lower) ? joint.limit.lower : -Math.PI;
    const upper = Number.isFinite(joint.limit?.upper) ? joint.limit.upper : Math.PI;
    joint.setJointValue(clamp(value, lower, upper));
  }

  objectForHumanJoint(name) {
    const linkName = HUMAN_TO_G1_LINK[normalizedName(name)];
    return linkName ? this.robot?.links?.[linkName] : null;
  }

  worldPositionForHumanJoint(name, target) {
    const object = this.objectForHumanJoint(name);
    if (!object) return null;
    const normalized = normalizedName(name);
    if (["head", "neck", "lefthand", "righthand", "lefttoe", "righttoe"].includes(normalized)) {
      return new THREE.Box3().setFromObject(object).getCenter(target);
    }
    return object.getWorldPosition(target);
  }

  updateSelection(name) {
    const position = name ? this.worldPositionForHumanJoint(name, this.selectionMarker.position) : null;
    this.selectionMarker.visible = Boolean(position && this.modelRoot.visible);
  }

  projectJoints(frame) {
    const rectangle = this.canvas.getBoundingClientRect();
    const world = new THREE.Vector3();
    return (frame.jointNames || []).map((name, index) => {
      if (!frame.validity?.[index] || !this.worldPositionForHumanJoint(name, world)) return null;
      const projected = world.clone().project(this.camera);
      return {
        x: (projected.x * 0.5 + 0.5) * rectangle.width,
        y: (-projected.y * 0.5 + 0.5) * rectangle.height,
        depth: projected.z,
        index,
        name,
      };
    }).filter(Boolean);
  }
}
