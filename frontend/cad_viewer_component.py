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

def generate_cad_viewer_html(file_name, file_ext, base64_content, height=450):
    """
    Three.js + occt-import-js 기반의 정적(Static) 3D CAD 형상 이미지 뷰어 HTML을 생성합니다.
    (회전, 줌, 팬 등의 조작 없이 고정된 아이소메트릭 3D 뷰의 깔끔한 형상 이미지로 표시)
    """
    is_step = file_ext in ['.step', '.stp']
    
    html_code = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; user-select: none; }}
            body {{ 
                margin: 0; padding: 0; overflow: hidden; 
                background: linear-gradient(135deg, #181920 0%, #20222a 100%); 
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; 
                color: #e0e0e0;
                border-radius: 12px;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }}
            #viewer-wrapper {{ 
                width: 100%; height: {height}px; position: relative; 
                display: flex; justify-content: center; align-items: center;
                pointer-events: none; /* 마우스 인터랙션 비활성화 */
            }}
            #canvas-container {{ 
                width: 100%; height: 100%; position: absolute; top: 0; left: 0;
            }}
            #info-tag {{
                position: absolute; top: 12px; left: 14px; z-index: 10;
                background: rgba(20, 22, 30, 0.85); backdrop-filter: blur(8px);
                padding: 6px 14px; border-radius: 20px; border: 1px solid rgba(255, 255, 255, 0.15);
                font-size: 12px; font-weight: 500; display: flex; align-items: center; gap: 6px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            }}
            #loading-overlay {{
                position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                background: rgba(18, 20, 26, 0.95); display: flex; flex-direction: column;
                justify-content: center; align-items: center; z-index: 20;
                transition: opacity 0.3s ease;
            }}
            .cad-spinner {{
                border: 3px solid rgba(255, 255, 255, 0.1);
                border-top: 3px solid #4dabf7; border-radius: 50%;
                width: 38px; height: 38px; animation: spin 0.9s linear infinite;
            }}
            @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        </style>
        <!-- Three.js (r128) & STLLoader -->
        <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/STLLoader.js"></script>
        <!-- OpenCASCADE WebAssembly engine for STEP -->
        <script src="https://cdn.jsdelivr.net/npm/occt-import-js@0.0.22/dist/occt-import-js.js"></script>
    </head>
    <body>
        <div id="viewer-wrapper">
            <div id="canvas-container"></div>
            
            <div id="info-tag">
                <span>🖼️</span>
                <span><strong>{file_name}</strong></span>
                <span style="opacity: 0.6; font-size: 10px;">({file_ext.upper()})</span>
            </div>

            <div id="loading-overlay">
                <div class="cad-spinner"></div>
                <p id="loading-text" style="margin-top: 12px; font-size: 12px; color: #90caf9; font-weight: 500;">
                    형상 이미지 생성 중...
                </p>
            </div>
        </div>

        <script>
            let scene, camera, renderer;
            let modelGroup = new THREE.Group();
            const isStep = {'true' if is_step else 'false'};
            const base64Data = "{base64_content}";

            function init() {{
                const container = document.getElementById('canvas-container');
                scene = new THREE.Scene();
                scene.background = new THREE.Color(0x1a1b22);
                scene.add(modelGroup);

                // Professional CAD Studio Lighting
                const ambientLight = new THREE.AmbientLight(0xffffff, 0.75);
                scene.add(ambientLight);

                const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.95);
                dirLight1.position.set(150, 250, 150);
                scene.add(dirLight1);

                const dirLight2 = new THREE.DirectionalLight(0x90caf9, 0.6);
                dirLight2.position.set(-150, -100, -150);
                scene.add(dirLight2);

                const dirLight3 = new THREE.DirectionalLight(0xffffff, 0.4);
                dirLight3.position.set(0, 200, -200);
                scene.add(dirLight3);

                // Subtle ground grid
                const grid = new THREE.GridHelper(300, 30, 0x37474f, 0x212121);
                grid.position.y = -0.1;
                scene.add(grid);

                camera = new THREE.PerspectiveCamera(40, container.clientWidth / container.clientHeight, 0.1, 5000);

                renderer = new THREE.WebGLRenderer({{ antialias: true, alpha: true, preserveDrawingBuffer: true }});
                renderer.setSize(container.clientWidth, container.clientHeight);
                renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
                renderer.shadowMap.enabled = true;
                container.appendChild(renderer.domElement);

                // Load CAD model
                if (isStep) {{
                    loadStepModel(base64Data);
                }} else {{
                    loadStlModel(base64Data);
                }}
            }}

            function renderScene() {{
                renderer.render(scene, camera);
            }}

            function fitCameraToIsometric(object) {{
                const box = new THREE.Box3().setFromObject(object);
                if (box.isEmpty()) return;

                const center = box.getCenter(new THREE.Vector3());
                const size = box.getSize(new THREE.Vector3());
                const maxDim = Math.max(size.x, size.y, size.z);
                const fov = camera.fov * (Math.PI / 180);
                let cameraDist = Math.abs(maxDim / 2 / Math.tan(fov / 2)) * 1.55;

                // 고정된 최적의 3D 아이소메트릭 각도 (대각선 위에서 바라보는 뷰)
                camera.position.set(
                    center.x + cameraDist * 0.85, 
                    center.y + cameraDist * 0.65, 
                    center.z + cameraDist * 0.85
                );
                camera.lookAt(center);
                camera.updateProjectionMatrix();

                // 렌더링 실행
                renderScene();
            }}

            function hideLoading() {{
                const overlay = document.getElementById('loading-overlay');
                overlay.style.opacity = '0';
                setTimeout(() => overlay.style.display = 'none', 300);
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

                    const defaultMat = new THREE.MeshStandardMaterial({{
                        color: 0x90caf9,
                        metalness: 0.3,
                        roughness: 0.45,
                        side: THREE.DoubleSide
                    }});

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

                        let meshMat = defaultMat.clone();
                        if (meshData.color) {{
                            meshMat.color = new THREE.Color(meshData.color[0], meshData.color[1], meshData.color[2]);
                        }}

                        const mesh = new THREE.Mesh(geometry, meshMat);
                        modelGroup.add(mesh);

                        // Sharp Feature Edges
                        const edges = new THREE.EdgesGeometry(geometry, 24);
                        const edgeLine = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({{ 
                            color: 0x1565c0, 
                            linewidth: 1.2, 
                            transparent: true, 
                            opacity: 0.9 
                        }}));
                        modelGroup.add(edgeLine);
                    }}

                    fitCameraToIsometric(modelGroup);
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

                    const material = new THREE.MeshStandardMaterial({{
                        color: 0x64b5f6,
                        metalness: 0.35,
                        roughness: 0.45,
                        side: THREE.DoubleSide
                    }});

                    const mesh = new THREE.Mesh(geometry, material);
                    modelGroup.add(mesh);

                    const edges = new THREE.EdgesGeometry(geometry, 24);
                    const edgeLine = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({{ 
                        color: 0x0d47a1, 
                        linewidth: 1.2, 
                        transparent: true, 
                        opacity: 0.9 
                    }}));
                    modelGroup.add(edgeLine);

                    fitCameraToIsometric(modelGroup);
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

def render_cad_viewer(part_code, height=450):
    """
    Streamlit 화면에 해당 Part의 정적 CAD 형상 이미지를 렌더링합니다.
    """
    file_name, file_ext, file_bytes = get_cad_file_data(part_code)
    
    if not file_bytes:
        st.info(f"ℹ️ Part `{part_code}`에 등록된 CAD 모델(.step, .stp, .stl) 파일이 없습니다.")
        return False
        
    b64_content = base64.b64encode(file_bytes).decode('utf-8')
    html_code = generate_cad_viewer_html(file_name, file_ext, b64_content, height=height)
    
    components.html(html_code, height=height + 20, scrolling=False)
    return True
