import os
import base64
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy.orm import Session
import sys

# Project root 경로 등록
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.append(BACKEND_DIR)

from DB.database import SessionLocal
from DB.models import CadFileArchive
from vault_manager import get_abs_vault_path, VAULT_ROOT

def get_cad_file_data(part_code):
    """
    특정 Part Code에 해당하는 CAD 파일명, 확장자 및 바이너리 데이터를 로드합니다.
    (DB BLOB 우선, Vault/로컬 디스크 파일 경로 보조)
    """
    with SessionLocal() as session:
        cad_record = session.query(CadFileArchive).filter_by(part_code=part_code).first()
        
        file_name = None
        file_bytes = None
        file_ext = None
        
        # 1. DB 레코드 확인
        if cad_record:
            file_name = cad_record.file_name or f"{part_code}.step"
            file_ext = os.path.splitext(file_name)[1].lower()
            
            if cad_record.file_content:
                file_bytes = cad_record.file_content
            elif cad_record.file_path:
                abs_p = get_abs_vault_path(cad_record.file_path)
                if abs_p and os.path.exists(abs_p):
                    with open(abs_p, "rb") as f:
                        file_bytes = f.read()
                        
        # 2. Vault 디렉토리 탐색
        if not file_bytes:
            cad_vault_dir = os.path.join(VAULT_ROOT, "cad_models", f"Part_{part_code}")
            if os.path.exists(cad_vault_dir):
                for f in os.listdir(cad_vault_dir):
                    if f.lower().endswith(('.step', '.stp', '.stl')):
                        file_name = f
                        file_ext = os.path.splitext(f)[1].lower()
                        with open(os.path.join(cad_vault_dir, f), "rb") as fp:
                            file_bytes = fp.read()
                        break
                        
        # 3. machining_raw_data 탐색
        if not file_bytes:
            raw_dir = os.path.join(PROJECT_ROOT, "machining_raw_data")
            if os.path.exists(raw_dir):
                for proj in os.listdir(raw_dir):
                    p_path = os.path.join(raw_dir, proj, part_code, "CAD_Files")
                    if os.path.exists(p_path):
                        for f in os.listdir(p_path):
                            if f.lower().endswith(('.step', '.stp', '.stl')):
                                file_name = f
                                file_ext = os.path.splitext(f)[1].lower()
                                with open(os.path.join(p_path, f), "rb") as fp:
                                    file_bytes = fp.read()
                                break
                    if file_bytes:
                        break
                        
        return file_name, file_ext, file_bytes

def generate_cad_viewer_html(file_name, file_ext, base64_content, height=520):
    """
    Three.js + OrbitControls + occt-import-js(WASM) 기반의 3D 인터랙티브 CAD 뷰어 HTML을 생성합니다.
    (360도 자유/자동 회전, 메시/와이어프레임 구조 토글, 치수 HUD, 시점 프리셋 지원)
    """
    is_step = file_ext in ['.step', '.stp']
    
    html_code = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; user-select: none; }}
            body {{ 
                margin: 0; padding: 0; overflow: hidden; 
                background: linear-gradient(135deg, #12131a 0%, #1c1e28 100%); 
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans KR", sans-serif; 
                color: #e2e8f0;
                border-radius: 12px;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }}
            #viewer-wrapper {{ 
                width: 100%; height: {height}px; position: relative; 
                display: flex; justify-content: center; align-items: center;
            }}
            #canvas-container {{ 
                width: 100%; height: 100%; position: absolute; top: 0; left: 0;
                cursor: grab;
            }}
            #canvas-container:active {{
                cursor: grabbing;
            }}

            /* Top Header Bar */
            .top-bar {{
                position: absolute; top: 12px; left: 14px; right: 14px;
                display: flex; justify-content: space-between; align-items: flex-start;
                pointer-events: none; z-index: 10;
            }}
            .glass-panel {{
                background: rgba(18, 22, 34, 0.85);
                backdrop-filter: blur(10px);
                -webkit-backdrop-filter: blur(10px);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 10px;
                box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
                pointer-events: auto;
            }}

            #file-badge {{
                padding: 6px 14px;
                font-size: 13px; font-weight: 600;
                display: flex; align-items: center; gap: 8px;
            }}
            .format-tag {{
                background: #1971c2; color: #ffffff;
                font-size: 10px; font-weight: 700;
                padding: 2px 7px; border-radius: 6px;
                letter-spacing: 0.5px;
            }}

            #hud-panel {{
                padding: 8px 14px;
                font-size: 11px;
                line-height: 1.5;
                color: #94a3b8;
                text-align: right;
            }}
            #hud-panel span.val {{
                color: #38bdf8;
                font-weight: 600;
                font-family: monospace;
            }}

            /* Bottom Toolbar */
            .bottom-controls {{
                position: absolute; bottom: 12px; left: 50%; transform: translateX(-50%);
                display: flex; flex-direction: column; align-items: center; gap: 6px;
                z-index: 10; pointer-events: none; width: 95%; max-width: 780px;
            }}
            .toolbar {{
                display: flex; flex-wrap: wrap; justify-content: center; align-items: center;
                gap: 5px; padding: 6px 10px; pointer-events: auto;
            }}
            .btn-group {{
                display: inline-flex; align-items: center;
                background: rgba(255, 255, 255, 0.05);
                padding: 2px; border-radius: 8px;
                border: 1px solid rgba(255, 255, 255, 0.08);
            }}
            .tool-btn {{
                background: transparent; border: none; outline: none;
                color: #cbd5e1; font-size: 11.5px; font-weight: 500;
                padding: 5px 10px; border-radius: 6px;
                cursor: pointer; display: inline-flex; align-items: center; gap: 5px;
                transition: all 0.15s ease-in-out;
            }}
            .tool-btn:hover {{
                background: rgba(255, 255, 255, 0.12);
                color: #ffffff;
            }}
            .tool-btn.active {{
                background: #0284c7;
                color: #ffffff;
                font-weight: 600;
                box-shadow: 0 0 10px rgba(2, 132, 199, 0.5);
            }}
            .tool-divider {{
                width: 1px; height: 18px;
                background: rgba(255, 255, 255, 0.15);
                margin: 0 4px;
            }}

            /* Mouse Instruction Caption */
            #interaction-guide {{
                font-size: 11px; color: #64748b;
                background: rgba(15, 23, 42, 0.7);
                padding: 3px 12px; border-radius: 12px;
                border: 1px solid rgba(255, 255, 255, 0.05);
                pointer-events: none;
                display: flex; align-items: center; gap: 8px;
            }}
            #interaction-guide b {{ color: #cbd5e1; }}

            /* Color Picker Pill */
            .color-dot {{
                width: 14px; height: 14px; border-radius: 50%;
                cursor: pointer; border: 1.5px solid rgba(255,255,255,0.4);
                transition: transform 0.15s;
            }}
            .color-dot:hover {{
                transform: scale(1.25);
            }}

            /* Loading Overlay */
            #loading-overlay {{
                position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                background: rgba(15, 18, 28, 0.94); display: flex; flex-direction: column;
                justify-content: center; align-items: center; z-index: 30;
                transition: opacity 0.3s ease;
            }}
            .cad-spinner {{
                border: 3px solid rgba(255, 255, 255, 0.1);
                border-top: 3px solid #38bdf8; border-radius: 50%;
                width: 44px; height: 44px; animation: spin 0.85s linear infinite;
            }}
            @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        </style>
        <!-- Three.js (r128) -->
        <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
        <!-- OrbitControls for 360-degree rotation -->
        <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
        <!-- STLLoader -->
        <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/STLLoader.js"></script>
        <!-- OpenCASCADE WebAssembly engine for STEP -->
        <script src="https://cdn.jsdelivr.net/npm/occt-import-js@0.0.22/dist/occt-import-js.js"></script>
    </head>
    <body>
        <div id="viewer-wrapper">
            <div id="canvas-container"></div>
            
            <!-- Top Header -->
            <div class="top-bar">
                <div id="file-badge" class="glass-panel">
                    <span>📐</span>
                    <span><strong>{file_name}</strong></span>
                    <span class="format-tag">{file_ext.upper().replace('.', '')}</span>
                </div>
                
                <div id="hud-panel" class="glass-panel">
                    <div>크기 (W×D×H): <span class="val" id="hud-dim">계산 중...</span></div>
                    <div>메시: <span class="val" id="hud-triangles">-</span> Tris &nbsp;|&nbsp; 정점: <span class="val" id="hud-vertices">-</span> Verts</div>
                </div>
            </div>

            <!-- Bottom Controls -->
            <div class="bottom-controls">
                <div class="toolbar glass-panel">
                    <!-- 360 Auto Rotation Toggle -->
                    <button id="btn-autorotate" class="tool-btn" onclick="toggleAutoRotate()" title="360도 연속 자동 회전 토글">
                        <span>🔄</span> <span>360° 자동회전</span>
                    </button>

                    <div class="tool-divider"></div>

                    <!-- Rendering Mode Switcher (Solid / Mesh / Solid+Mesh / X-Ray) -->
                    <div class="btn-group">
                        <button id="btn-mode-solid" class="tool-btn active mode-btn" onclick="setRenderMode('solid')" title="솔리드 표면 + 외곽선">
                            <span>🧊</span> <span>솔리드</span>
                        </button>
                        <button id="btn-mode-mesh" class="tool-btn mode-btn" onclick="setRenderMode('mesh')" title="삼각형 폴리곤 메시/와이어프레임 구조만 보기">
                            <span>🕸️</span> <span>메시(Wire)</span>
                        </button>
                        <button id="btn-mode-solid-mesh" class="tool-btn mode-btn" onclick="setRenderMode('solid_mesh')" title="솔리드 표면 + 메시 와이어 중첩">
                            <span>📦</span> <span>솔리드+메시</span>
                        </button>
                        <button id="btn-mode-xray" class="tool-btn mode-btn" onclick="setRenderMode('xray')" title="반투명 X-Ray 투시 모드">
                            <span>👻</span> <span>X-Ray</span>
                        </button>
                    </div>

                    <div class="tool-divider"></div>

                    <!-- Camera View Presets -->
                    <div class="btn-group">
                        <button class="tool-btn" onclick="setCameraView('iso')" title="등각 투영 시점">
                            <span>📐</span> <span>등각</span>
                        </button>
                        <button class="tool-btn" onclick="setCameraView('top')" title="상면도 (Top)">
                            <span>⬆️</span> <span>상면</span>
                        </button>
                        <button class="tool-btn" onclick="setCameraView('front')" title="정면도 (Front)">
                            <span>➡️</span> <span>정면</span>
                        </button>
                        <button class="tool-btn" onclick="setCameraView('side')" title="측면도 (Side)">
                            <span>↗️</span> <span>측면</span>
                        </button>
                    </div>

                    <div class="tool-divider"></div>

                    <!-- Bounding Box Helper Toggle -->
                    <button id="btn-box" class="tool-btn" onclick="toggleBoxHelper()" title="외곽 치수 바운딩 박스 표시 토글">
                        <span>📏</span> <span>치수박스</span>
                    </button>

                    <!-- Color Presets -->
                    <div style="display: inline-flex; align-items: center; gap: 5px; margin-left: 4px;">
                        <div class="color-dot" style="background: #38bdf8;" onclick="setPartColor('#38bdf8')" title="테크 블루"></div>
                        <div class="color-dot" style="background: #cfd8dc;" onclick="setPartColor('#cfd8dc')" title="메탈릭 스틸"></div>
                        <div class="color-dot" style="background: #fbbf24;" onclick="setPartColor('#fbbf24')" title="골드 브라스"></div>
                        <div class="color-dot" style="background: #4ade80;" onclick="setPartColor('#4ade80')" title="에메랄드"></div>
                    </div>

                    <!-- Fullscreen Toggle -->
                    <button class="tool-btn" onclick="toggleFullscreen()" title="전체화면 확대">
                        <span>⛶</span>
                    </button>
                </div>

                <div id="interaction-guide">
                    <span>🖱️ <b>좌클릭 드래그:</b> 360° 자유 회전</span>
                    <span>•</span>
                    <span><b>우클릭 드래그:</b> 시점 이동(Pan)</span>
                    <span>•</span>
                    <span><b>마우스 휠:</b> 줌인/줌아웃</span>
                </div>
            </div>

            <!-- Loading Overlay -->
            <div id="loading-overlay">
                <div class="cad-spinner"></div>
                <p id="loading-text" style="margin-top: 14px; font-size: 13px; color: #38bdf8; font-weight: 600;">
                    3D CAD 모델 파싱 및 메시 생성 중...
                </p>
            </div>
        </div>

        <script>
            let scene, camera, renderer, controls;
            let modelGroup = new THREE.Group();
            let boxHelper = null;
            let loadedMeshes = []; // array of {{ solidMesh, edgeLines, wireframeLines, mat, defaultColor, geometry }}
            let modelCenter = new THREE.Vector3();
            let modelRadius = 100;
            let currentRenderMode = 'solid';
            let currentColor = '#38bdf8';

            const isStep = {'true' if is_step else 'false'};
            const base64Data = "{base64_content}";

            function init() {{
                const container = document.getElementById('canvas-container');
                const width = container.clientWidth || window.innerWidth;
                const height = container.clientHeight || {height};

                scene = new THREE.Scene();
                scene.background = new THREE.Color(0x12141c);
                scene.add(modelGroup);

                // Lighting
                const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
                scene.add(ambientLight);

                const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.9);
                dirLight1.position.set(200, 300, 200);
                scene.add(dirLight1);

                const dirLight2 = new THREE.DirectionalLight(0x38bdf8, 0.5);
                dirLight2.position.set(-200, -150, -200);
                scene.add(dirLight2);

                const dirLight3 = new THREE.DirectionalLight(0xffffff, 0.4);
                dirLight3.position.set(0, 250, -250);
                scene.add(dirLight3);

                // Technical Floor Grid
                const grid = new THREE.GridHelper(400, 40, 0x334155, 0x1e293b);
                grid.position.y = -0.1;
                scene.add(grid);

                // Camera & Renderer
                camera = new THREE.PerspectiveCamera(40, width / height, 0.1, 10000);

                renderer = new THREE.WebGLRenderer({{ antialias: true, alpha: true, preserveDrawingBuffer: true }});
                renderer.setSize(width, height);
                renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
                renderer.shadowMap.enabled = true;
                container.appendChild(renderer.domElement);

                // OrbitControls for 360-degree rotation & smooth damping
                controls = new THREE.OrbitControls(camera, renderer.domElement);
                controls.enableDamping = true;
                controls.dampingFactor = 0.06;
                controls.screenSpacePanning = true;
                controls.autoRotate = false;
                controls.autoRotateSpeed = 2.5;
                controls.maxPolarAngle = Math.PI; // Full 360-degree vertical & horizontal freedom

                window.addEventListener('resize', onWindowResize);

                // Start Render Loop
                animate();

                // Load CAD model
                if (isStep) {{
                    loadStepModel(base64Data);
                }} else {{
                    loadStlModel(base64Data);
                }}
            }}

            function animate() {{
                requestAnimationFrame(animate);
                if (controls) controls.update();
                renderer.render(scene, camera);
            }}

            function onWindowResize() {{
                const container = document.getElementById('canvas-container');
                if (!container) return;
                const width = container.clientWidth;
                const height = container.clientHeight;
                camera.aspect = width / height;
                camera.updateProjectionMatrix();
                renderer.setSize(width, height);
            }}

            function hideLoading() {{
                const overlay = document.getElementById('loading-overlay');
                if (overlay) {{
                    overlay.style.opacity = '0';
                    setTimeout(() => overlay.style.display = 'none', 300);
                }}
            }}

            function updateModelMetadata() {{
                const box = new THREE.Box3().setFromObject(modelGroup);
                if (box.isEmpty()) return;

                modelCenter = box.getCenter(new THREE.Vector3());
                const size = box.getSize(new THREE.Vector3());
                const sphere = box.getBoundingSphere(new THREE.Sphere());
                modelRadius = sphere.radius || Math.max(size.x, size.y, size.z) / 2 || 50;

                // Update HUD info
                const dimText = `${{size.x.toFixed(1)}} × ${{size.y.toFixed(1)}} × ${{size.z.toFixed(1)}} mm`;
                document.getElementById('hud-dim').innerText = dimText;

                let totalTriangles = 0;
                let totalVertices = 0;
                loadedMeshes.forEach(item => {{
                    if (item.geometry) {{
                        if (item.geometry.index) {{
                            totalTriangles += item.geometry.index.count / 3;
                        }} else if (item.geometry.attributes.position) {{
                            totalTriangles += item.geometry.attributes.position.count / 3;
                        }}
                        if (item.geometry.attributes.position) {{
                            totalVertices += item.geometry.attributes.position.count;
                        }}
                    }}
                }});

                document.getElementById('hud-triangles').innerText = Math.round(totalTriangles).toLocaleString();
                document.getElementById('hud-vertices').innerText = Math.round(totalVertices).toLocaleString();

                // Create Bounding Box Helper
                boxHelper = new THREE.BoxHelper(modelGroup, 0x00e5ff);
                boxHelper.visible = false;
                scene.add(boxHelper);

                // Set initial Isometric Camera View
                setCameraView('iso');
            }}

            function setCameraView(view) {{
                if (!controls || !camera) return;
                const dist = modelRadius * 2.3;

                if (view === 'iso') {{
                    camera.position.set(
                        modelCenter.x + dist * 0.75, 
                        modelCenter.y + dist * 0.65, 
                        modelCenter.z + dist * 0.75
                    );
                }} else if (view === 'top') {{
                    camera.position.set(modelCenter.x, modelCenter.y + dist * 1.3, modelCenter.z + 0.0001);
                }} else if (view === 'front') {{
                    camera.position.set(modelCenter.x, modelCenter.y, modelCenter.z + dist * 1.3);
                }} else if (view === 'side') {{
                    camera.position.set(modelCenter.x + dist * 1.3, modelCenter.y, modelCenter.z);
                }}

                controls.target.copy(modelCenter);
                camera.lookAt(modelCenter);
                camera.updateProjectionMatrix();
                controls.update();
            }}

            function toggleAutoRotate() {{
                if (!controls) return;
                controls.autoRotate = !controls.autoRotate;
                const btn = document.getElementById('btn-autorotate');
                if (controls.autoRotate) {{
                    btn.classList.add('active');
                }} else {{
                    btn.classList.remove('active');
                }}
            }}

            function setRenderMode(mode) {{
                currentRenderMode = mode;
                
                document.querySelectorAll('.mode-btn').forEach(btn => btn.classList.remove('active'));
                const targetBtn = document.getElementById('btn-mode-' + mode.replace('_', '-'));
                if (targetBtn) targetBtn.classList.add('active');

                loadedMeshes.forEach(item => {{
                    if (mode === 'solid') {{
                        item.solidMesh.visible = true;
                        item.mat.wireframe = false;
                        item.mat.opacity = 1.0;
                        item.mat.transparent = false;
                        item.edgeLines.visible = true;
                        item.wireframeLines.visible = false;
                    }} else if (mode === 'mesh') {{
                        // 메시 / 와이어프레임 구조만 표시
                        item.solidMesh.visible = false;
                        item.edgeLines.visible = false;
                        item.wireframeLines.visible = true;
                        item.wireframeLines.material.color.set(0x00e5ff);
                        item.wireframeLines.material.opacity = 0.95;
                    }} else if (mode === 'solid_mesh') {{
                        // 솔리드 표면 + 메시 와이어 중첩 표시
                        item.solidMesh.visible = true;
                        item.mat.wireframe = false;
                        item.mat.opacity = 1.0;
                        item.mat.transparent = false;
                        item.edgeLines.visible = true;
                        item.wireframeLines.visible = true;
                        item.wireframeLines.material.color.set(0x00e5ff);
                        item.wireframeLines.material.opacity = 0.45;
                    }} else if (mode === 'xray') {{
                        // X-Ray 반투명 투시 모드
                        item.solidMesh.visible = true;
                        item.mat.wireframe = false;
                        item.mat.opacity = 0.35;
                        item.mat.transparent = true;
                        item.edgeLines.visible = true;
                        item.wireframeLines.visible = false;
                    }}
                }});
            }}

            function toggleBoxHelper() {{
                if (boxHelper) {{
                    boxHelper.visible = !boxHelper.visible;
                    const btn = document.getElementById('btn-box');
                    if (boxHelper.visible) {{
                        btn.classList.add('active');
                    }} else {{
                        btn.classList.remove('active');
                    }}
                }}
            }}

            function setPartColor(hexColor) {{
                currentColor = hexColor;
                loadedMeshes.forEach(item => {{
                    item.mat.color.set(hexColor);
                }});
            }}

            function toggleFullscreen() {{
                const elem = document.getElementById('viewer-wrapper');
                if (!document.fullscreenElement) {{
                    if (elem.requestFullscreen) elem.requestFullscreen();
                }} else {{
                    if (document.exitFullscreen) document.exitFullscreen();
                }}
            }}

            async function loadStepModel(b64) {{
                try {{
                    const occt = await occtimportjs();
                    const binaryString = atob(b64);
                    const bytes = new Uint8Array(binaryString.length);
                    for (let i = 0; i < binaryString.length; i++) {{
                        bytes[i] = binaryString.charCodeAt(i);
                    }}

                    const result = occt.ReadStepFile(bytes, null);
                    if (!result.success || !result.meshes || result.meshes.length === 0) {{
                        throw new Error("STEP 형상 파싱 실패");
                    }}

                    for (let meshData of result.meshes) {{
                        const geometry = new THREE.BufferGeometry();
                        geometry.setAttribute('position', new THREE.Float32BufferAttribute(meshData.attributes.position.array, 3));
                        if (meshData.attributes.normal) {{
                            geometry.setAttribute('normal', new THREE.Float32BufferAttribute(meshData.attributes.normal.array, 3));
                        }} else {{
                            geometry.computeVertexNormals();
                        }}
                        if (meshData.index) {{
                            geometry.setIndex(new THREE.Uint32BufferAttribute(meshData.index.array, 1));
                        }}

                        let itemColor = currentColor;
                        if (meshData.color) {{
                            itemColor = new THREE.Color(meshData.color[0], meshData.color[1], meshData.color[2]);
                        }}

                        // 1. Solid Mesh
                        const mat = new THREE.MeshStandardMaterial({{
                            color: itemColor,
                            metalness: 0.35,
                            roughness: 0.45,
                            side: THREE.DoubleSide
                        }});
                        const solidMesh = new THREE.Mesh(geometry, mat);
                        modelGroup.add(solidMesh);

                        // 2. Sharp Feature Edges
                        const edgesGeom = new THREE.EdgesGeometry(geometry, 24);
                        const edgeMat = new THREE.LineBasicMaterial({{ 
                            color: 0x0c4a6e, 
                            linewidth: 1.2, 
                            transparent: true, 
                            opacity: 0.85 
                        }});
                        const edgeLines = new THREE.LineSegments(edgesGeom, edgeMat);
                        modelGroup.add(edgeLines);

                        // 3. Triangle Wireframe Lines (Mesh structure)
                        const wireGeom = new THREE.WireframeGeometry(geometry);
                        const wireMat = new THREE.LineBasicMaterial({{ 
                            color: 0x00e5ff, 
                            linewidth: 1.0, 
                            transparent: true, 
                            opacity: 0.75 
                        }});
                        const wireframeLines = new THREE.LineSegments(wireGeom, wireMat);
                        wireframeLines.visible = false;
                        modelGroup.add(wireframeLines);

                        loadedMeshes.push({{ solidMesh, edgeLines, wireframeLines, mat, defaultColor: itemColor, geometry }});
                    }}

                    updateModelMetadata();
                    hideLoading();
                }} catch (err) {{
                    console.error(err);
                    document.getElementById('loading-text').innerHTML = '⚠️ 형상 로딩 에러: ' + err.message;
                }}
            }}

            function loadStlModel(b64) {{
                try {{
                    const binaryString = atob(b64);
                    const bytes = new Uint8Array(binaryString.length);
                    for (let i = 0; i < binaryString.length; i++) {{
                        bytes[i] = binaryString.charCodeAt(i);
                    }}

                    const loader = new THREE.STLLoader();
                    const geometry = loader.parse(bytes.buffer);
                    geometry.computeVertexNormals();

                    // 1. Solid Mesh
                    const mat = new THREE.MeshStandardMaterial({{
                        color: currentColor,
                        metalness: 0.35,
                        roughness: 0.45,
                        side: THREE.DoubleSide
                    }});
                    const solidMesh = new THREE.Mesh(geometry, mat);
                    modelGroup.add(solidMesh);

                    // 2. Sharp Feature Edges
                    const edgesGeom = new THREE.EdgesGeometry(geometry, 24);
                    const edgeMat = new THREE.LineBasicMaterial({{ 
                        color: 0x0c4a6e, 
                        linewidth: 1.2, 
                        transparent: true, 
                        opacity: 0.85 
                    }});
                    const edgeLines = new THREE.LineSegments(edgesGeom, edgeMat);
                    modelGroup.add(edgeLines);

                    // 3. Triangle Wireframe Lines (Mesh structure)
                    const wireGeom = new THREE.WireframeGeometry(geometry);
                    const wireMat = new THREE.LineBasicMaterial({{ 
                        color: 0x00e5ff, 
                        linewidth: 1.0, 
                        transparent: true, 
                        opacity: 0.75 
                    }});
                    const wireframeLines = new THREE.LineSegments(wireGeom, wireMat);
                    wireframeLines.visible = false;
                    modelGroup.add(wireframeLines);

                    loadedMeshes.push({{ solidMesh, edgeLines, wireframeLines, mat, defaultColor: currentColor, geometry }});

                    updateModelMetadata();
                    hideLoading();
                }} catch (err) {{
                    console.error(err);
                    document.getElementById('loading-text').innerHTML = '⚠️ STL 파싱 에러: ' + err.message;
                }}
            }}

            window.onload = init;
        </script>
    </body>
    </html>
    """
    return html_code

def render_cad_viewer(part_code, height=520):
    """
    Streamlit 화면에 해당 Part의 3D 인터랙티브 CAD 뷰어(360도 회전/메시 모드)를 렌더링합니다.
    """
    file_name, file_ext, file_bytes = get_cad_file_data(part_code)
    
    if not file_bytes:
        st.info(f"ℹ️ Part `{part_code}`에 등록된 CAD 모델(.step, .stp, .stl) 파일이 없습니다.")
        return False
        
    b64_content = base64.b64encode(file_bytes).decode('utf-8')
    html_code = generate_cad_viewer_html(file_name, file_ext, b64_content, height=height)
    
    components.html(html_code, height=height + 25, scrolling=False)
    return True
