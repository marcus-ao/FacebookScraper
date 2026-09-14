import { Button, Result } from 'antd'
import { Link } from 'react-router'

import { PageTitle } from './PageTitle'

export function NotFound() {
  return (
    <>
      <PageTitle />
      <Result
        status="404"
        title="这个地址在审校台里没有对应的界面"
        subTitle="链接可能来自更早的版本，或者这一篇已经不在可打开的范围内。回到审校队列重新挑一篇。"
        extra={
          <Link to="/review">
            <Button type="primary">返回审校队列</Button>
          </Link>
        }
      />
    </>
  )
}
