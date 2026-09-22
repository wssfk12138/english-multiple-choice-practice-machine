import { reactive } from 'vue'
export const confirmation = reactive({ open:false, message:'' })
let settle:((value:boolean)=>void)|null=null
export function confirmDesktop(message:string):Promise<boolean> {
  if (settle) settle(false)
  confirmation.message=message; confirmation.open=true
  return new Promise(resolve=>{settle=resolve})
}
export function finishConfirmation(value:boolean) {
  confirmation.open=false; settle?.(value); settle=null
}
