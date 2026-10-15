using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.Hosting;

namespace Mole.API
{
    public class Program
    {
        public static void Main(string[] args)
        {
            Host.CreateDefaultBuilder(args) // Kestrel, IIS integration, appsettings + environment variables, logging
                .ConfigureWebHostDefaults(web => web.UseStartup<Startup>())
                .Build()
                .Run();
        }
    }
}
