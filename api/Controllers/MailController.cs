using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;
using Microsoft.Extensions.Options;
using Newtonsoft.Json;
using System.Net.Mail;
using System.Text.RegularExpressions;
using System.Linq;
using System;
using Microsoft.AspNetCore.Http;
using System.Net;

namespace Mole.API.Controllers
{
    [Produces("application/json")]
    [Route("__Mail")]
    public class MailController : Controller
    {
        private readonly ComputationManager manager;

        public MailController(IOptions<ComputationManager> optionsAccessor)
        {
            manager = optionsAccessor.Value;
        }

        [HttpPost]
        public ActionResult Post([FromBody] MailParameters p)
        {
            if (p == null) return Content(JsonConvert.SerializeObject(new { Error = "Mallformed parameter" }));
            if (!Regex.IsMatch(p.From, @"^\w+([-+.']\w+)*@\w+([-.]\w+)*\.\w+([-.]\w+)*$")) return Content(JsonConvert.SerializeObject(new { Error = $"Invalid mail address: {p.From}" }));

            try
            {
                SmtpClient client = new SmtpClient("smtp.gmail.com");
                MailAddress from = new MailAddress(p.From, "MOLE API help", System.Text.Encoding.UTF8);
                MailAddress to = new MailAddress("mole@upol.cz");


                using (MailMessage message = new MailMessage(from, to))
                {
                    message.Body =
                        new string[] {
                    "----------",
                    "MOLE API mailing service\n",
                    $"User {p.From} is asking for help with the following computation: https://mole.upol.cz/online/{p.ComputationId}/{p.SubmitId}",
                    "DO NOT reply to this email directly and Respond ASAP :)",
                    "----------\n",
                    p.Msg,
                    "\n",
                    "Have a nice day =)",
                    "MOLE API"
                        }
                        .Aggregate((a, b) => a + "\n" + b);


                    message.BodyEncoding = System.Text.Encoding.UTF8;
                    message.Subject = $"MOLE API | {p.From} asks for help.";
                    message.SubjectEncoding = System.Text.Encoding.UTF8;

                    client.EnableSsl = true;
                    client.Credentials = new NetworkCredential("webchemistryhelp", "W3bCh3m.");
                    client.Send(message);
                }

                return Content(JsonConvert.SerializeObject(new { Success = true, Msg = "The message has been succesfully sent." }));
            }
            catch (Exception e)
            {
                return Content(JsonConvert.SerializeObject(new { Success = false, Msg = $"[Error] {e.Message}" }));
            }
        }
    }
}
