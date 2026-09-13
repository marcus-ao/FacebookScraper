"""运营参数白名单与配置版本冲突。"""
from fastapi import APIRouter, HTTPException, Request

from core import operating_settings

router = APIRouter()


@router.get('/api/settings')
def get_settings():
    return operating_settings.read()


@router.put('/api/settings')
async def put_settings(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {'values', 'version'}:
            raise ValueError('请提交设置值和配置版本')
        return operating_settings.save(body['values'], body['version'])
    except operating_settings.SettingsConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
