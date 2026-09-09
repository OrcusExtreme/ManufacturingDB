import os
from DB.database import SessionLocal
from DB.models import Part, CadFileArchive
from vault_manager import save_to_vault
from integrity import sha256_bytes

from job_manager import get_or_create_job

def parse_cad(file_path, project_name, part_name):
    """
    CAD 파일(.step, .stp, .stl)을 파싱하여 Vault에 저장하고 DB에 삽입합니다.
    CAD 데이터는 특정 Job이 아닌 Part에 종속되므로 Part 레벨에서 저장합니다.
    """
    print(f"  -> [CAD Parser] 파일: {os.path.basename(file_path)} / Project: {project_name} / Part: {part_name}")
    
    db = SessionLocal()
    try:
        # 1. DB에 Part가 있는지 확인 후 없으면 생성 (부품은 이름으로 찾고 키는 번호를 쓴다)
        part = db.query(Part).filter(Part.part_name == part_name).first()
        if not part:
            part = Part(
                part_name=part_name,
                project_code=project_name,
                material_code="Unknown"
            )
            db.add(part)
            db.flush()
        part_code = part.part_code
            
        # 2. 이미 등록된 동일한 파일명의 CAD 파일이 해당 Part에 있는지 확인
        existing = db.query(CadFileArchive).filter(
            CadFileArchive.part_code == part_code,
            CadFileArchive.file_name == os.path.basename(file_path)
        ).first()
        
        if existing:
            print(f"    - [CAD Parser] {os.path.basename(file_path)} 이미 등록됨. 건너뜁니다.")
            return True

        ext = os.path.splitext(file_path)[1].lower().replace('.', '')
        file_name = os.path.basename(file_path)
        
        # 15MB 제한 확인
        MAX_SIZE = 15 * 1024 * 1024
        file_size = os.path.getsize(file_path)
        
        file_content_to_save = None
        if file_size <= MAX_SIZE:
            with open(file_path, 'rb') as f:
                file_content_to_save = f.read()
        else:
            print(f"    - [알림] CAD 파일({file_name}) 크기가 15MB를 초과하여 DB 내부 저장을 생략합니다.")
            
        # Vault 저장
        cad_vault_path = save_to_vault(file_path, "cad_models", f"Part_{part_name}", file_name)
        
        # 기존 동일 파일이 있는지 확인 (동일 확장자, 파일명 기준)
        existing_cad = db.query(CadFileArchive).filter(
            CadFileArchive.part_code == part_code,
            CadFileArchive.file_name == file_name
        ).first()
        
        cad_sha256 = sha256_bytes(file_content_to_save) if file_content_to_save else None

        if existing_cad:
            existing_cad.file_path = cad_vault_path
            existing_cad.file_content = file_content_to_save
            existing_cad.file_sha256 = cad_sha256
            print(f"    - CAD 파일 업데이트 완료 (Part: {part_name})")
        else:
            new_cad = CadFileArchive(
                part_code=part_code,
                file_name=file_name,
                file_type=ext,
                file_path=cad_vault_path,
                file_content=file_content_to_save,
                file_sha256=cad_sha256
            )
            db.add(new_cad)
            print(f"    - CAD 파일 삽입 완료 (Part: {part_name})")
            
        db.commit()
        return True

    except Exception as e:
        db.rollback()
        print(f"    - [오류] CAD 파싱 실패: {e}")
        return False
    finally:
        db.close()
