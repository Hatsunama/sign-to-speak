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
    const renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
    });
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

  return <canvas ref={canvasRef} className="h-full w-full" />;
}
