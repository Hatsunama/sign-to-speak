"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { buildHandMesh } from "@/lib/asl/mesh";
import type { Point } from "@/lib/asl/geometry";

export function HandMeshView({
  jointsRef,
  mode,
}: {
  jointsRef: React.RefObject<Point[] | null>;
  mode: "overlay" | "stage";
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    if (!webglAvailable()) {
      return drawFlatHand(canvas, jointsRef, mode);
    }
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        canvas,
        alpha: true,
        antialias: true,
      });
    } catch {
      return drawFlatHand(canvas, jointsRef, mode);
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    const scene = new THREE.Scene();
    const perspective = new THREE.PerspectiveCamera(32, 1, 0.01, 10);
    perspective.position.set(0.2, 0.14, 0.38);
    perspective.lookAt(0.01, 0.05, 0);
    const ortho = new THREE.OrthographicCamera(0, 1, 1, 0, -500, 500);
    const camera = mode === "stage" ? perspective : ortho;

    scene.add(new THREE.AmbientLight(0xfff4e8, 0.72));
    const key = new THREE.DirectionalLight(0xfff1dc, 1.25);
    key.position.set(0.4, 0.8, 1);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x9eb7d8, 0.38);
    fill.position.set(-0.7, -0.2, 0.5);
    scene.add(fill);

    const geometry = new THREE.BufferGeometry();
    const material = new THREE.MeshStandardMaterial({
      color: mode === "overlay" ? 0xf0c7a4 : 0xe4b08a,
      roughness: 0.46,
      metalness: 0.02,
      side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(geometry, material);
    scene.add(mesh);

    let frame = 0;
    const draw = () => {
      frame = window.requestAnimationFrame(draw);
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (width < 2 || height < 2) {
        return;
      }
      renderer.setSize(width, height, false);
      const joints = jointsRef.current;
      if (!joints || joints.length < 21) {
        mesh.visible = false;
        renderer.render(scene, camera);
        return;
      }
      const placed =
        mode === "overlay"
          ? joints.map((point) => ({ x: point.x, y: height - point.y, z: point.z }))
          : joints;
      const built = buildHandMesh(placed);
      if (!built) {
        mesh.visible = false;
        renderer.render(scene, camera);
        return;
      }
      geometry.setAttribute("position", new THREE.BufferAttribute(built.positions, 3));
      geometry.setIndex(new THREE.BufferAttribute(built.indices, 1));
      geometry.computeVertexNormals();
      mesh.visible = true;
      if (mode === "overlay") {
        ortho.left = 0;
        ortho.right = width;
        ortho.top = height;
        ortho.bottom = 0;
        ortho.updateProjectionMatrix();
      } else {
        perspective.aspect = width / height;
        perspective.updateProjectionMatrix();
      }
      renderer.render(scene, camera);
    };
    draw();
    return () => {
      window.cancelAnimationFrame(frame);
      geometry.dispose();
      material.dispose();
      renderer.dispose();
    };
  }, [jointsRef, mode]);

  return <canvas ref={canvasRef} className="block h-full w-full" />;
}

const HAND_EDGES: ReadonlyArray<readonly [number, number]> = [
  [0, 1],
  [1, 2],
  [2, 3],
  [3, 4],
  [0, 5],
  [5, 6],
  [6, 7],
  [7, 8],
  [0, 9],
  [9, 10],
  [10, 11],
  [11, 12],
  [0, 13],
  [13, 14],
  [14, 15],
  [15, 16],
  [0, 17],
  [17, 18],
  [18, 19],
  [19, 20],
  [5, 9],
  [9, 13],
  [13, 17],
];

function webglAvailable(): boolean {
  const probe = document.createElement("canvas");
  try {
    return Boolean(probe.getContext("webgl2") ?? probe.getContext("webgl"));
  } catch {
    return false;
  }
}

function drawFlatHand(
  canvas: HTMLCanvasElement,
  jointsRef: React.RefObject<Point[] | null>,
  mode: "overlay" | "stage",
): () => void {
  const context = canvas.getContext("2d");
  if (!context) {
    return () => undefined;
  }
  let frame = 0;
  const draw = () => {
    frame = window.requestAnimationFrame(draw);
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (width < 2 || height < 2) {
      return;
    }
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const joints = jointsRef.current;
    if (!joints || joints.length < 21) {
      return;
    }
    const placed = joints.map((point) => placeFlat(point, joints, mode, width, height));
    context.lineWidth = Math.max(3, Math.min(width, height) * 0.012);
    context.lineCap = "round";
    context.strokeStyle = "#e4b08a";
    context.fillStyle = "#f0c7a4";
    for (const [from, to] of HAND_EDGES) {
      const start = placed[from];
      const end = placed[to];
      if (!start || !end) {
        continue;
      }
      context.beginPath();
      context.moveTo(start.x, start.y);
      context.lineTo(end.x, end.y);
      context.stroke();
    }
    for (const point of placed) {
      if (!point) {
        continue;
      }
      context.beginPath();
      context.arc(point.x, point.y, context.lineWidth * 0.85, 0, Math.PI * 2);
      context.fill();
    }
  };
  draw();
  return () => window.cancelAnimationFrame(frame);
}

function placeFlat(
  point: Point,
  joints: Point[],
  mode: "overlay" | "stage",
  width: number,
  height: number,
): { x: number; y: number } {
  if (mode === "overlay") {
    return { x: point.x, y: point.y };
  }
  const wrist = joints[0] ?? { x: 0, y: 0, z: 0 };
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const joint of joints) {
    minX = Math.min(minX, joint.x - wrist.x);
    maxX = Math.max(maxX, joint.x - wrist.x);
    minY = Math.min(minY, joint.y - wrist.y);
    maxY = Math.max(maxY, joint.y - wrist.y);
  }
  const span = Math.max(maxX - minX, maxY - minY, 1e-4);
  const scale = Math.min(width, height) * 0.72 / span;
  return {
    x: width / 2 + (point.x - wrist.x - (minX + maxX) / 2) * scale,
    y: height / 2 - (point.y - wrist.y - (minY + maxY) / 2) * scale,
  };
}
